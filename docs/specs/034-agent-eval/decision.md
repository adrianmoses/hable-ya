# Decision Record: Agent Eval (Synthetic Learner + Opus Judge + Orchestrator)

| Field | Value |
|---|---|
| id | 034 |
| status | implemented |
| created | 2026-04-25 |
| spec | [spec.md](./spec.md) |
| covers roadmap | #034 + #035 + #036 (bundle) |

---

## Context

Spec 034 was authored as a tightly self-contained design doc — file layout,
function signatures, test plan, cost model, and validate-before-proceeding
gates were all specified up front. The implementation mostly sequences and
grounds it. A few real-world frictions shaped the work:

- **Anthropic SDK was declared in the `eval` extra but not installed.** The
  smoke environment needed `uv sync --extra eval` plus a manual `python -m
  spacy download es_core_news_sm` after — `uv sync` evicts the spaCy model
  because it's not in the dependency tree.
- **Opus filled `rationale: {}` on the first smoke run.** The spec's
  `dict[str, str]` type doesn't require any keys. The schema was tightened
  to a structured `Rationale` Pydantic model with five required `min_length=1`
  string fields, and `JUDGE_SYSTEM_VERSION` bumped from `"1"` to `"2"` to
  invalidate the empty-rationale cache files that had already been written.
- **llama.cpp's gemma-4-e4b at `temperature=0.0` is not fully deterministic
  across runs.** About one agent turn per session diverges, which cascades
  through the cache. Spec OQ#6 claimed cached re-runs cost $0; in practice
  they cost the divergent suffix's re-rolled learner turns plus a fresh
  judge call (~$2-4 per re-run, not $0).
- **`_profile_from_snapshot` already existed privately in
  `hable_ya/pipeline/prompts/builder.py:63`.** The "lift" the spec called
  for was a promotion + rename, not net-new code.
- **Anthropic 529 overloads hit the first concurrent smoke run.** Dropping
  to `--concurrency 1` for the persona that failed (and partial cache reuse
  from the earlier attempt) produced a clean three-persona smoke.

The spec's two gates ran cleanly: aggregation refactor green before any
`eval/agent/` code landed (Gate 1); 3-persona smoke produced coherent
transcripts, plausible judge rationales, and 100% `log_turn` emission
before scaling to 15 personas (Gate 2).

---

## Decision

Implemented a session-level eval harness as bundled #034 + #035 + #036,
shipped on branch `spec-agent-eval` over seven commits. Five components:

1. **Shared aggregation core** (`hable_ya/learner/aggregations.py`):
   `compute_snapshot` is the single source of truth for the rolling-mean +
   top-N rules. `LearnerProfileRepo.get` (production path) and
   `ProfileAccumulator` (eval path) both call it. A DB-backed equivalence
   test guards against silent drift.
2. **In-process profile accumulator** (`eval/agent/accumulator.py`):
   hybrid storage — `deque` for the rolling window, `Counter` and
   `dict[str, datetime]` for errors/vocab — feeding `compute_snapshot`.
3. **Opus-driven synthetic learner** (`eval/agent/synthetic_learner.py`):
   persona-conditioned, role-flipped transcript so Opus sees the agent's
   turns as `user` messages. Disk-cached by
   `sha256(version + persona.id + canonical_transcript)`.
4. **Opus session judge** (`eval/agent/opus_judge.py`): emits a
   `SessionVerdict` with five integer 1–5 dims, a structured `Rationale`
   model with all five fields required, a computed `overall`, and a
   post-hoc `stop_reason` label. Cached identically.
5. **Orchestrator + comparator** (`eval/agent/run_agent_eval.py`,
   `eval/agent/compare.py`): drives 15 hand-authored personas (3 per band,
   A1–C1) end-to-end against llama.cpp using the *same*
   `finetune.format.render_system_prompt` the runtime uses; emits
   `agent_results.json` shaped after `eval/run_eval.py`'s output;
   compare.py mirrors `eval/compare.py` for baseline-vs-candidate diffs.

The entire `eval/agent/**` surface plus the eight new test files clear
strict mypy and ruff without expanding the per-file E501 ignore list.

---

## Alternatives Considered

### Decision Point 1: `compute_snapshot` input shape

The spec gave an illustrative signature `compute_snapshot(turns, *, band,
sessions_completed, ...)`, but `LearnerProfileRepo.get` aggregates *in SQL*
(`AVG(L1_used)`, weighted fluency mean) and reads pre-aggregated
`error_counts` / `vocabulary_items` tables. The illustrative shape doesn't
fit production cleanly.

**Option A:** Take normalized `list[TurnRecord]` (spec's hint).
- Pros: matches the spec verbatim; clean abstraction over "turns".
- Cons: forces the repo to either denormalize SQL aggregates back into
  individual turn rows, or to bypass the pure function for top-N.

**Option B:** Take pre-tallied inputs — `Sequence[bool]`,
`Sequence[FluencySignal]`, `Counter[str]`, `Mapping[str, datetime]`.
- Pros: matches the natural shape on both sides — repo populates from
  small SELECTs (window rows + the existing pre-aggregated tables);
  accumulator populates from in-memory counters as `log_turn` records
  arrive.
- Cons: signature has nine keyword params instead of four.

**Chosen:** B. The illustrative signature was illustrative. Drift between
eval and runtime would have been silently introduced if the repo had to
denormalize.

### Decision Point 2: SessionVerdict.rationale shape

The spec specified `rationale: dict[str, str]`. The first smoke run
revealed Opus filled `rationale: {}` for every session.

**Option A:** Keep `dict[str, str]`, document expected keys.
- Pros: matches the spec verbatim.
- Cons: doesn't actually enforce the keys; Opus exploited the looseness;
  consumers (compare.py, future analysis) can't rely on rationale presence.

**Option B:** Structured `Rationale` Pydantic model with five required
`min_length=1` string fields.
- Pros: schema enforcement at parse time; `messages.parse` sends a stricter
  schema to Opus; rationales are populated reliably (verified on the
  follow-up smoke).
- Cons: divergence from spec; required `JUDGE_SYSTEM_VERSION` bump.

**Chosen:** B. The spec contract said "one short sentence per dimension";
the structured model enforces what the spec described.

### Decision Point 3: Cache primitive — single vs per-consumer

**Option A:** Hand-rolled, separate caches in `synthetic_learner.py` and
`opus_judge.py` — close to call sites.
- Pros: trivial to reason about each in isolation.
- Cons: identical code in two places; tests double up.

**Option B:** One `JsonDiskCache` primitive in `eval/agent/_cache.py` with
a `key_prefix` parameter; both consumers wrap it.
- Pros: single point of test coverage; namespace separation via prefix;
  easy to add a third consumer later.
- Cons: ~30 lines of generality.

**Chosen:** B. The cleanup pass later pulled `canonical_transcript()` and
`cached_system_block()` into the same module after both consumers ended up
with identical copies of those helpers too — the same instinct, applied
twice.

### Decision Point 4: ProfileAccumulator state — incremental vs recompute

**Option A:** Pure recompute — store all `TurnRecord`s, recompute snapshot
from scratch each call.
- Pros: minimal state; "snapshot is a fold over turns".
- Cons: O(N) per agent turn = O(N²) over a session.

**Option B:** Hybrid — `deque(maxlen=window)` for L1/fluency (auto-evicts);
`Counter` + last-seen dicts for errors/vocab (mirrors the production
pre-aggregated tables).
- Pros: O(1) per ingest; matches production state exactly; the
  equivalence test against the DB-backed repo proves it.
- Cons: more state to keep coherent; the snapshot() call still does the
  top-N sort, but over bounded counters.

**Chosen:** B. The equivalence test is the guard.

### Decision Point 5: Smoke gate — author 3 personas first vs all 15

The spec's validate-before-proceeding section explicitly called for 3-then-12.

**Option A:** Author all 15 upfront, run smoke against full set.
- Pros: one-shot.
- Cons: persona-stability is unknown; if Opus over-steers on the schema,
  12 personas need to be revised before the smoke clears.

**Option B:** Author 3 covering one band each, smoke them, then 12 more
once the schema and runtime are validated.
- Pros: spec-compliant; the schema iteration during Phase 4 (rationale
  shape) would have meant rewriting 15 cache entries instead of 3.
- Cons: two passes through persona authoring.

**Chosen:** B (per spec gate). Vindicated when the schema needed a
mid-stream tightening — only the 3 smoke personas were affected.

### Decision Point 6: Cost preview — comprehensive prediction vs honest worst-case

The first cut of `_cost_preview` tried to predict cached vs uncached for
both learner and judge per persona. The judge prediction relied on
`f.name.startswith(p.id)`, but cache file names are `sha256` hashes that
never start with persona ids — the check always returned 0.

**Option A:** Comprehensive — dry-run the session enough to compute cache
keys, then check disk.
- Pros: tight estimate.
- Cons: runs an entire simulated session just to predict cost.

**Option B:** Honest worst-case — only the first learner turn has a
deterministic key (empty transcript + persona.id). Judge calls are always
assumed uncached. Persona-with-cached-first-turn correlates strongly with
"most subsequent turns will hit cache too" empirically.
- Pros: simple, honest about its limits.
- Cons: gives only an upper bracket.

**Chosen:** B during Phase 4 cleanup. The post-run observed cost is logged
separately, so drift from the estimate is visible.

---

## Tradeoffs

- **The shared `compute_snapshot` shifted some aggregation work from SQL
  to Python.** With `window_turns=20`, the rolling-mean computation is
  trivial; the top-N picking was already Python-side via SQL `ORDER BY +
  LIMIT`. Net Python overhead is negligible. Tradeoff: the repo's SQL
  query is one extra round trip for `error_counts` (was implicitly joined;
  is now a separate `SELECT category, count, last_seen_at FROM
  error_counts`). Acceptable: the table is small.
- **Disk caches grow without bound.** `eval/agent/_cache.py` has no
  cleanup story. Per spec, this is feature, not bug — cache misses from
  prior model versions stay around as a corpus of examples for inspection.
- **llama.cpp non-determinism breaks the "free re-runs" assumption.** Per
  spec OQ#6, subsequent runs over the same model+personas were estimated
  at ~$0. In practice they cost ~$0.50–$2 because one agent turn per
  session typically diverges, cascading through the learner cache (the
  judge always re-rolls because the transcript hash changed). Documented
  in commits and below in Spec Gaps.
- **Threshold values in `compare.py` are placeholders (3.5 across all
  dims).** Per spec, real thresholds calibrate from the first baseline
  distribution; out of scope for this implementation.
- **Persona stability tested at 3, not 15.** The smoke ran clean on the 3
  smoke personas. The remaining 12 are authored to a similar template but
  unvalidated against Opus until the first full baseline run. Spec
  acknowledges this risk under "Confidence: Medium" #1.
- **Opus persona-stability and judge calibration are non-deterministic at
  the run level.** Score *deltas* (baseline vs candidate) are usable
  immediately; absolute scores will drift run-to-run. Spec is explicit on
  this.

---

### Spec Divergence

The implementation matched the spec on every architectural decision (file
layout, model boundaries, caching, CI scope, no Postgres in eval path,
etc). The five places it diverged:

| Spec Said | What Was Built | Reason |
|---|---|---|
| `SessionVerdict.rationale: dict[str, str]` | `rationale: Rationale` (structured model with 5 required `min_length=1` fields) | First smoke showed Opus returned `rationale: {}` because `dict[str, str]` doesn't require keys. The structured model enforces the contract the spec described in prose. |
| `JUDGE_SYSTEM_VERSION = "1"` | `JUDGE_SYSTEM_VERSION = "2"` | The schema change above required cache invalidation. Bumping the version is the spec's documented invalidation mechanism. |
| `compute_snapshot(turns, *, band, sessions_completed, window_turns, top_errors, top_vocab) -> LearnerProfileSnapshot` | Same return type and most kwargs, but takes pre-tallied inputs (`l1_used_flags`, `fluency_signals`, `error_counter`, `error_last_seen`, `vocab_last_seen`) instead of a single `turns` argument; drops `window_turns` (caller pre-trims) | The repo aggregates via SQL on pre-aggregated tables; passing pre-tallied inputs lets both call sites populate naturally. The spec called this signature illustrative in the resolution of Open Question #1, so the divergence is anticipated. |
| Cost preview "breakdown by (cached vs. uncached) learner turns based on the existing cache state on disk" — implies per-learner-turn cache prediction | Honest worst-case estimate: only the first learner turn has a deterministic cache key. Judge calls always assumed uncached. | Predicting downstream learner-turn cache hits requires running the session to compute the transcript-dependent keys. The cleaner bracket is more useful than a fragile prediction. Post-run cost is logged separately for drift detection. |
| `--dry-run` "renders persona → transcript-request pairs without hitting either endpoint so prompt authoring is testable offline" | `--dry-run` resolves personas + themes and prints `<persona-id> → band=X theme=Y`, hits no endpoint | The intermediate-state render-pair view would require wiring a stub through the agent caller. The bare resolution view satisfies the spec's stated goal (offline authoring sanity check) without that complexity. |

Two augmentations beyond the spec, not divergences:

- `compare.py` includes a "Stop Reasons" histogram table in addition to the
  per-dimension / per-band / per-error-pattern tables.
- `_cache.py` exposes `canonical_transcript()` and `cached_system_block()`
  helpers that the spec's file layout doesn't list. Both arose from the
  cleanup pass — the synthetic learner and judge had identical copies.

---

## Spec Gaps Exposed

1. **`SessionVerdict.rationale: dict[str, str]` is too permissive.** The
   spec describes the rationale as "one-sentence reason per dim" but the
   typed shape doesn't enforce keys or non-empty values. Opus exploited
   this. Spec should specify a structured model (or document the required
   keys + min_length) up front. Resolved here by introducing a `Rationale`
   model and bumping `JUDGE_SYSTEM_VERSION`.

2. **llama.cpp at temperature=0.0 is not deterministic in practice.** Spec
   OQ#6 stated "Subsequent runs (same model, same personas) ... Total ≈
   $0.00 (only llama.cpp inference, which is free locally)". Observed:
   one agent turn per session typically diverges across runs, cascading
   through the learner cache and forcing a fresh judge call. Subsequent
   runs cost ~$0.50–$2 in practice. Worth a spec-revision note: the
   determinism assumption holds for greedy sampling at a single
   batch/seed but not across server restarts or under `--parallel 4`
   batched scheduling.

3. **The `compute_snapshot` signature in the spec is not implementable
   verbatim.** The repo's SQL aggregation made the actual signature
   different. The spec acknowledges this in Open Question #1 ("the
   aggregation core lifted into a shared pure function") but the
   illustrative signature in §How is misleading. Future specs that lift
   logic out of a SQL-backed repo should sketch the signature with the
   call-site shapes in mind.

4. **`log_turn` emission rate observation contradicts project memory.**
   Project memory warns that "deployed fine-tuned Gemma misses ~1 call in
   5" (~80% emission). The 3-persona smoke showed 36/36 = 100% emission.
   Either the emission rate has improved on the current GGUF, or the
   memory is stale, or the no-thinking + low-token regime is more
   reliable than the production setting. Worth re-checking when the first
   full 15-persona baseline runs.

5. **The cost preview can't honestly predict downstream cache hits**
   without running the session. Spec's design ("breakdown by cached vs.
   uncached") implies a precision the cache-key structure doesn't allow.
   Worst-case bracket + post-run observed cost is the honest pair; spec
   should reflect that.

6. **The `eval` extra in pyproject.toml installs anthropic but not
   `es_core_news_sm`.** `uv sync --extra eval` removes the spaCy model
   because it's a manual download step. CI handles this with a separate
   `python -m spacy download es_core_news_sm` step, but local devs
   running `uv sync` after a fresh checkout will hit a `OSError [E050]`
   from spaCy until they re-download. Worth a README note.

---

## Test Evidence

**Phase 0 + Phase 1 + Phase 2 unit + Phase 3 unit + smoke + Phase 4 +
Phase 5 unit tests:** 63 spec-034-related tests pass (full suite: 229
total).

```
$ .venv/bin/python -m pytest tests/test_aggregations_shared.py \
    tests/test_learner_profile.py tests/test_agent_personas.py \
    tests/test_agent_cache.py tests/test_agent_accumulator.py \
    tests/test_agent_learner.py tests/test_agent_judge_prompts.py \
    tests/test_agent_aggregates.py tests/test_agent_learner_smoke.py \
    tests/test_agent_judge_smoke.py -v

tests/test_aggregations_shared.py::test_empty_inputs_return_neutral_defaults PASSED [  1%]
tests/test_aggregations_shared.py::test_rolling_means_average_over_window PASSED [  3%]
tests/test_aggregations_shared.py::test_top_errors_order_by_count_descending PASSED [  4%]
tests/test_aggregations_shared.py::test_top_errors_tiebreak_by_last_seen_descending PASSED [  6%]
tests/test_aggregations_shared.py::test_top_vocab_order_by_last_seen_descending PASSED [  7%]
tests/test_aggregations_shared.py::test_truncates_to_top_n_limits PASSED [  9%]
tests/test_aggregations_shared.py::test_does_not_re_window_l1_or_fluency PASSED [ 11%]
tests/test_learner_profile.py::test_cold_start_snapshot_uses_neutral_defaults PASSED [ 12%]
tests/test_learner_profile.py::test_window_limits_to_most_recent_turns PASSED [ 14%]
tests/test_learner_profile.py::test_repo_delegates_to_compute_snapshot PASSED [ 15%]
tests/test_learner_profile.py::test_increment_session_count PASSED       [ 17%]
tests/test_learner_profile.py::test_set_band_persists PASSED             [ 19%]
tests/test_agent_personas.py::test_persona_validates_minimal_payload PASSED [ 20%]
tests/test_agent_personas.py::test_unknown_scenario_domain_for_band_fails PASSED [ 22%]
tests/test_agent_personas.py::test_scenario_domain_in_wrong_band_fails PASSED [ 23%]
tests/test_agent_personas.py::test_unknown_error_pattern_fails PASSED    [ 25%]
tests/test_agent_personas.py::test_extra_fields_rejected PASSED          [ 26%]
tests/test_agent_personas.py::test_load_personas_reads_directory PASSED  [ 28%]
tests/test_agent_personas.py::test_load_authored_smoke_personas PASSED   [ 30%]
tests/test_agent_personas.py::test_load_personas_fails_on_first_invalid_file PASSED [ 31%]
tests/test_agent_personas.py::test_allowed_error_patterns_covers_smoke_personas PASSED [ 33%]
tests/test_agent_personas.py::test_snapshot_to_profile_composes_with_render_system_prompt PASSED [ 34%]
tests/test_agent_cache.py::test_get_returns_none_for_missing_key PASSED  [ 36%]
tests/test_agent_cache.py::test_put_then_get_round_trip PASSED           [ 38%]
tests/test_agent_cache.py::test_keys_isolated PASSED                     [ 39%]
tests/test_agent_cache.py::test_creates_directory_if_missing PASSED      [ 41%]
tests/test_agent_cache.py::test_key_prefix_namespaces_files PASSED       [ 42%]
tests/test_agent_cache.py::test_rejects_path_traversal_in_key PASSED     [ 44%]
tests/test_agent_cache.py::test_files_are_human_readable_json PASSED     [ 46%]
tests/test_agent_accumulator.py::test_neutral_snapshot_when_no_turns_ingested PASSED [ 47%]
tests/test_agent_accumulator.py::test_ingest_updates_rolling_means PASSED [ 49%]
tests/test_agent_accumulator.py::test_window_evicts_oldest_turns PASSED  [ 50%]
tests/test_agent_accumulator.py::test_error_counter_orders_top_n_by_count PASSED [ 52%]
tests/test_agent_accumulator.py::test_vocab_last_seen_tracks_most_recent PASSED [ 53%]
tests/test_agent_accumulator.py::test_repeated_lemma_updates_last_seen PASSED [ 55%]
tests/test_agent_accumulator.py::test_empty_error_categories_does_not_pollute_counter PASSED [ 57%]
tests/test_agent_accumulator.py::test_accumulator_matches_repo_for_same_log_turn_sequence PASSED [ 58%]
tests/test_agent_learner.py::test_system_prompt_mentions_band_scenario_and_errors PASSED [ 60%]
tests/test_agent_learner.py::test_role_flip_swaps_assistant_and_user PASSED [ 61%]
tests/test_agent_learner.py::test_cache_key_differs_per_persona PASSED   [ 63%]
tests/test_agent_learner.py::test_cache_key_changes_when_transcript_grows PASSED [ 65%]
tests/test_agent_learner.py::test_opening_utterance_short_circuit_skips_api PASSED [ 66%]
tests/test_agent_learner.py::test_cache_hit_skips_api PASSED             [ 68%]
tests/test_agent_learner.py::test_cache_miss_calls_api_and_caches_result PASSED [ 69%]
tests/test_agent_judge_prompts.py::test_session_verdict_validates_bounds PASSED [ 71%]
tests/test_agent_judge_prompts.py::test_session_verdict_rejects_out_of_range PASSED [ 73%]
tests/test_agent_judge_prompts.py::test_session_verdict_rejects_unknown_stop_reason PASSED [ 74%]
tests/test_agent_judge_prompts.py::test_overall_round_trips_through_dump PASSED [ 76%]
tests/test_agent_judge_prompts.py::test_judge_user_prompt_includes_persona_and_numbered_turns PASSED [ 77%]
tests/test_agent_judge_prompts.py::test_judge_system_prompt_mentions_all_dims_and_stop_reason PASSED [ 79%]
tests/test_agent_judge_prompts.py::test_cache_key_changes_with_transcript_perturbation PASSED [ 80%]
tests/test_agent_judge_prompts.py::test_cache_key_stable_for_same_inputs PASSED [ 82%]
tests/test_agent_judge_prompts.py::test_judge_session_cache_hit_skips_api_call PASSED [ 84%]
tests/test_agent_aggregates.py::test_empty_returns_empty_dict PASSED     [ 85%]
tests/test_agent_aggregates.py::test_overall_and_by_dimension_means PASSED [ 87%]
tests/test_agent_aggregates.py::test_by_band_groups_correctly PASSED     [ 88%]
tests/test_agent_aggregates.py::test_by_error_pattern_one_session_can_count_under_multiple PASSED [ 90%]
tests/test_agent_aggregates.py::test_stop_reasons_counted PASSED         [ 92%]
tests/test_agent_aggregates.py::test_filter_personas_glob PASSED         [ 93%]
tests/test_agent_aggregates.py::test_filter_personas_comma_list PASSED   [ 95%]
tests/test_agent_aggregates.py::test_filter_personas_glob_plus_exact PASSED [ 96%]
tests/test_agent_learner_smoke.py::test_learner_responds_to_a_single_agent_turn PASSED [ 98%]
tests/test_agent_judge_smoke.py::test_judge_returns_well_formed_verdict_for_canned_transcript PASSED [100%]

============================== 63 passed in 7.87s ==============================
```

The two `_smoke` tests fired live Opus calls; they skip cleanly when
`ANTHROPIC_API_KEY` is unset (verified) and pass when set (verified above).

**Spec Gate 2 — end-to-end smoke run** (3 personas, fine-tuned Gemma at
`localhost:8080`, full prompt, `--no-thinking`):

```
$ python -m eval.agent.run_agent_eval --base-url http://localhost:8080 \
    --output /tmp/smoke_agent.json --no-thinking --max-tokens 512 \
    --concurrency 1
Cost preview (3 personas, avg 12.0 turns): learner uncached first-turn=0,
worst-case ~$0.18
Wrote /tmp/smoke_agent.json — 3 sessions, overall=4.0
```

| Persona | Band | Turns | log_turn / Turns | Wall-clock | Overall |
|---|---|---|---|---|---|
| `a1_ser_estar_cafeteria` | A1 | 24 | 12/12 | 37.85s | 3.2 |
| `a2_gender_agreement_restaurante` | A2 | 24 | 12/12 | 44.51s | 4.6 |
| `b1_preterite_imperfect_recuerdos` | B1 | 24 | 12/12 | 53.85s | 4.2 |

Aggregate signal:
```
overall: {mean: 4.0, n: 3}
by_dimension:
  pedagogical_flow:      mean=3.33
  level_consistency:     mean=5.00
  recast_naturalness:    mean=2.67   ← lowest, real signal
  learner_production_space: mean=4.67
  coherence:             mean=4.33
by_band:  A1=3.2  A2=4.6  B1=4.2
by_error_pattern:
  gender_agreement:    4.6 (n=1)
  preterite_imperfect: 4.2 (n=1)
  ser_estar:           3.7 (n=2)
stop_reasons: budget_reached=3
```

A representative judge rationale (a1_ser_estar_cafeteria,
recast_naturalness=2):

> "Learner repeatedly uses 'es' for estar (el café es caliente, dónde es,
> todo es bien) but agent never recasts with 'está'."

That's exactly the kind of session-level signal turn-level eval cannot
expose — a fine-tuned model that recasts isolated turns correctly per
fixtures still misses recasts in running conversation.

**Sanity check — the eval discriminates between prompt configs.**
Same persona (a2_gender_agreement_restaurante), full prompt vs minimal
prompt:

```
$ python -m eval.agent.run_agent_eval --base-url http://localhost:8080 \
    --output /tmp/smoke_minimal.json --no-thinking --max-tokens 512 \
    --concurrency 1 --minimal-prompt --personas a2_gender_agreement_restaurante
Wrote /tmp/smoke_minimal.json — 1 sessions, overall=3.4
```

Drop of 1.2 points (4.6 → 3.4) on the same persona confirms the eval
signal responds to prompt changes.

**Identity diff** confirms the comparator is well-formed:
```
$ python -m eval.agent.compare /tmp/smoke_agent.json /tmp/smoke_agent.json
                Per-Error-Pattern Overall
┌─────────────────────┬───────┬───────┬───────┬─────────┐
│ Error Pattern       │ Run A │ Run B │ Delta │ n (A/B) │
├─────────────────────┼───────┼───────┼───────┼─────────┤
│ gender_agreement    │  4.60 │  4.60 │  0.00 │     1/1 │
│ preterite_imperfect │  4.20 │  4.20 │  0.00 │     1/1 │
│ ser_estar           │  3.70 │  3.70 │  0.00 │     2/2 │
└─────────────────────┴───────┴───────┴───────┴─────────┘
```

**Lint + type:** ruff and strict mypy pass over `eval/agent/**` plus the
eight new test files without expanding the per-file E501 ignore list.

```
$ uv run ruff check eval/agent/ ...
All checks passed!

$ uv run mypy eval/agent/ ...
Success: no issues found in 64 source files
```
