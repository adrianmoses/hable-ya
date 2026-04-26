# Decision Record: Learner Leveling — Initial Placement + Auto-Update via Model-Emitted Per-Turn CEFR Band

| Field | Value |
|---|---|
| id | 049 |
| status | implemented |
| created | 2026-04-26 |
| spec | [spec.md](./spec.md) |

---

## Context

Spec 029 built the learner-profile substrate (`turns`, `error_counts`,
`vocabulary_items`, AGE graph, `LearnerProfileRepo.set_band`) but left
the band itself static at the bootstrap value. Without a band signal in
the runtime, neither initial placement (#050) nor auto-leveling (#049)
could function — the prompt builder's register guidance was always
"A2".

The product framing in `OVERVIEW.md` puts the model in the role of
pedagogical assessor. The pre-049 `log_turn` schema operationalised that
for fluency, L1 reliance, and error patterns, but stopped one field
short of the assessor's primary output: the learner's CEFR band on this
turn. Adding the band field is asking the model to emit the conclusion
it is already implicitly drawing — not introducing a new judgement.

Two factors shaped the work beyond the spec:

1. **Time-boxed prompt-engineering bet.** The fine-tuned Gemma 4 was
   trained on the prior 4-field `log_turn` schema. The user explicitly
   chose prompt-only first; a fine-tune pass on the new field is
   deferred to a follow-up if the regression bar isn't met. The runtime
   substrate (schema, placement, leveling, audit) has to be correct
   regardless of how well the model emits the field — so all of the
   non-emission code paths (placement abstain, leveling no-data, sink
   counters) had to land before any judgement on emission quality is
   possible.
2. **Code-review pass surfaced concrete duplications.** A post-merge
   review against the working tree flagged seven cases of duplicated
   constants/helpers (band midpoints, validation predicates, ALL_BANDS
   tuple, cold-start prompt assembly). Those were consolidated into a
   new `hable_ya/learner/bands.py` before this decision was written.

## Decision

Closed the band loop end-to-end, with the model as the assessor of
record:

- **Schema**: `LOG_TURN_TOOL` gains a required
  `cefr_band: enum["A1","A2","B1","B2","C1"]` parameter; the runtime
  parser tolerates missing or out-of-enum and degrades to `None` on the
  observation, incrementing a new sink counter `band_missing`. Per-turn
  bands persist on `turns.cefr_band TEXT NULL`.
- **Prompt**: a single `BAND_RUBRIC` source in
  `hable_ya/pipeline/prompts/render.py` renders into both the tool
  description (one-line gloss per band) and a new
  `## Assessing the learner's level` section in the system prompt
  (paragraph per band). The cold-start branch uses a rewritten four-step
  diagnostic ladder (introduction → daily life → past → opinion).
- **Placement (#050)**: end-of-session-1, the modal `cefr_band` over
  non-NULL turns drives `set_band` and a `band_history` row with
  `reason='placement'`. Below `placement_min_valid_turns=3` non-NULL
  bands the policy abstains; the learner stays uncalibrated and
  re-enters the diagnostic on the next session. `is_calibrated` is
  derived from "a `band_history` row with `reason='placement'` exists",
  not from `sessions_completed > 0`.
- **Auto-leveling (#049)**: end-of-session for calibrated learners
  reads the last `leveling_window_sessions=3` sessions' non-null
  `cefr_band` turns, computes the rolling mean of band-as-numeric
  (`A1=0.1`, `A2=0.3`, `B1=0.5`, `B2=0.7`, `C1=0.9`), and applies
  asymmetric hysteresis: K=3 promote-target sessions → promote, K=4
  demote-target sessions → demote, with demotion additionally requiring
  the rolling mean to cross the lower-band bucket center (no clock-start
  on borderline). Floor (A1) and ceiling (C1) are no-ops.
- **Audit**: `band_history (id, from_band, to_band, reason, signals,
  changed_at)` table; one row per band change. `learner_profile` gains
  `stable_sessions_at_band` (hysteresis counter) and
  `last_band_change_at`.
- **Regression CLI**: `scripts/replay_placement.py` runs the 25
  `cold_start` fixtures through the live llama.cpp endpoint, parses
  emitted `cefr_band` per turn, applies `place_band`, reports per-fixture
  rows + per-band aggregates + 5×5 confusion matrix + `band_missing`
  count, exits non-zero if accuracy < 0.75 or MAE > 0.20. CI step is
  advisory (`continue-on-error: true`) in this slice's landing PR; the
  promote-to-blocking decision happens pre-merge against an actual
  llama.cpp run.

The pure-function layer (`hable_ya.learner.leveling.policy`) is
deterministic and DB-free; the async wrapper
(`hable_ya.learner.leveling.service.LevelingService`) handles the
asyncpg I/O and writes the audit row + profile mutation in one
transaction.

---

## Alternatives Considered

### Primary band signal: model emission vs. heuristic reverse-engineering

**Option A: Heuristic weighted score from existing 4-field signals.**
Reverse-engineer a band from `fluency_signal` + `L1_used` + error
density + vocabulary diversity. The pre-049 spec sketch went this way.

- Pros: Works on the existing fine-tuned schema with no prompt change;
  no risk of model under-emission.
- Cons: Reinvents pedagogical assessment in code; brittle weights need
  tuning; obscures whether the model is doing its job at all; conflicts
  with the OVERVIEW framing of the model as assessor.

**Option B: Model emits `cefr_band` directly via the tool schema (chosen).**

- Pros: Matches the product framing; the model already does the
  assessment work for fluency/L1/errors, so adding the conclusion is
  cheap; the emission-rate signal itself is diagnostic ("did the model
  do its job?"); a fine-tune pass on the field is a clear next step if
  needed.
- Cons: Untrained field on the existing fine-tune — emission may drop,
  or the model may emit coherent-but-wrong calibration; the regression
  bar (`accuracy ≥ 0.75`) may not be reachable without a follow-up
  fine-tune.

**Chosen: Option B**, per user direction. The CI gate ships advisory
to absorb the regression-bar uncertainty.

### Heuristic fallback when the model omits `cefr_band`

**Option A: Reverse-engineer the band from secondary signals when missing.**

- Pros: No abstain path; placement always produces a band.
- Cons: Papers over "the model isn't doing its job" — the exact metric
  we want visible to decide whether a fine-tune is needed. Adds the
  brittle weighting table we rejected for the primary signal.

**Option B: Abstain (chosen).** If fewer than `min_valid_turns=3`
diagnostic turns carry a valid band, `place_band` returns `None`,
placement is skipped, and the learner re-enters the diagnostic on the
next session. The `band_missing` sink counter surfaces the under-emission
rate.

**Chosen: Option B.** Cleaner failure mode and a usable signal for the
fine-tune decision.

### Placement aggregation: modal vs. mean

**Option A: Mean band-as-numeric over the diagnostic turns.**

- Pros: Smooths single-turn outliers; same mechanism as auto-leveling.
- Cons: With only four turns, one strong outlier (e.g., the model
  emits A1 on a B2 learner's silent turn) can pull the mean across a
  bucket boundary.

**Option B: Modal band with tie-break to higher (chosen).**

- Pros: Robust to one outlier; tie-break-up gives the learner the
  benefit of the doubt — auto-leveling demotes later if wrong; mirrors
  how a human assessor reads four samples.
- Cons: Same four turns can produce a 4-way split, in which case
  modal is degenerate (handled by the `min_valid_turns` floor — if no
  band has ≥2 votes, placement still picks the highest from the count
  by tie-break, which is acceptable).

**Chosen: Option B.** Modal for placement (one decision over a small
sample), mean for leveling (trend over a larger window). The two
operate on the same field but differ by temporal window.

### Hysteresis design

**Option A: Symmetric K (e.g., K=3 both directions).**

- Pros: Simpler.
- Cons: A bad week shouldn't knock the learner down at the same speed
  a good week promotes them; demotion is psychologically heavier.

**Option B: Asymmetric K with crossing margin (chosen).** K=3 promote,
K=4 demote, plus demotion additionally requires the rolling mean to
cross at least into the lower-band bucket center (not just the
boundary).

- Pros: Slow to demote; borderline session doesn't start the demotion
  clock at all; reflects the asymmetric stakes.
- Cons: One more setting to tune; the K values are intuition not data.

**Chosen: Option B.** Settings (`leveling_promote_consecutive`,
`leveling_demote_consecutive`) make the values tunable without code
changes.

### `is_calibrated` source

**Option A: `sessions_completed > 0` (the pre-049 derivation).**

- Pros: Already wired through `snapshot_to_profile`; no schema work.
- Cons: A completed session that produced no `log_turn` calls
  (pathological but possible) would flip `is_calibrated` even though no
  actual placement ran.

**Option B: `EXISTS (band_history WHERE reason='placement')` (chosen).**

- Pros: Keys on the actual placement event; survives a degenerate
  session; one-row check.
- Cons: Required threading an explicit `is_calibrated` parameter
  through `snapshot_to_profile` (eval-accumulator path passes `False`).

**Chosen: Option B.** The semantics now match what the field is
called.

### CI regression gate posture

**Option A: Block the merge until the bar is met.**

- Pros: Ensures merged code clears the bar; clean.
- Cons: If prompt engineering plateaus below 0.75 accuracy, the
  substrate (schema, placement, leveling, audit) is still correct and
  worth landing; blocking creates a stuck state where neither the
  follow-up fine-tune nor anything else can build on this slice.

**Option B: Ship advisory (`continue-on-error: true`), promote to
blocking pre-merge if the bar is met (chosen).**

- Pros: Substrate lands either way; the CI signal is preserved as a
  visible trend; the promotion-to-blocking is a one-line PR if the
  fine-tune lifts emission quality.
- Cons: A passing CI run no longer means the regression bar is met —
  reviewers must read the per-band table.

**Chosen: Option B**, per Open Question 3 in the spec.

---

## Tradeoffs

- **Untrained field on the existing fine-tune.** The fine-tuned Gemma
  was trained on the 4-field `log_turn` schema. Adding `cefr_band` is a
  prompt-only change. Emission rate may degrade (the deployed ~80%
  baseline could drop further) and the bands emitted may be coherent
  but miscalibrated. The CI gate is advisory specifically to absorb
  this uncertainty; the runtime degrades cleanly via the abstain path
  when the model under-emits.
- **Hysteresis windows are intuition.** K=3 / K=4 feel right but the
  session-to-session variance of model-emitted bands is unmeasured.
  Real-world tuning is enabled by the settings but the defaults are a
  guess.
- **Placement is single-session.** Four turns is a thin sample;
  spec-correct mode-with-tie-break-up + the A2 floor keep the worst-case
  bounded, but the placed band is wrong about as often as a human first-
  meeting estimate. Auto-leveling is the recovery path.
- **Theme-aware leveling is not implemented.** The policy reads only
  the rolling band mean + hysteresis state; the AGE graph
  (`(:Learner)-[:ENGAGED_WITH]->(:Scenario {band})`) is not consulted.
  Theme bias in the diagnostic could skew the band — accepted as a
  known limitation per Non-Goals.
- **Multi-language calibration is not implemented.** The rubric is
  Spanish-from-English; a Portuguese-L1 learner whose Portuguese leaks
  would be misread. Per OVERVIEW "Not multi-language at launch".
- **Two extra DB round-trips per session-end on the calibrated path.**
  `is_calibrated_async` + `current_band` were collapsed to share one
  acquired connection in `end_session`, but `LevelingService.run_*`
  acquires its own connection (it manages its own transactions).
  End-of-session is not on the user-perceived hot path — the WS handler
  has already closed by then — but a tighter design would push the
  reads into one round-trip if this ever became a contention point.
- **Cold-start prompt is rendered at `default_learner_band` ("A2"),
  not the persona band.** On a fresh DB the runtime's `register`
  guidance reads at A2 throughout the diagnostic, so register guidance
  in the diagnostic doesn't reflect the learner's actual level. This
  matches what the runtime emits and was the latent bug fixed in the
  cleanup pass — replay_placement.py was previously rendering at the
  fixture's `true_band`, which would have over-stated emission accuracy
  on B/C-band fixtures.

### Spec Divergence

| Spec Said | What Was Built | Reason |
|---|---|---|
| Pure-function policy lives in a new `hable_ya/learner/leveling/policy.py` module with `BAND_TO_FLOAT` and `BAND_BUCKETS` constants | Same module; the constants moved to a new `hable_ya/learner/bands.py` shared with `render.py` and `profile.py`, re-exported from `policy.py` for compatibility | Code-review pass found the same dict / tuple defined in three places (policy.py, profile.py as `_BAND_MIDPOINT`, render.py as `band_from_production_level`'s inline boundaries). Consolidation eliminated five `band in {"A1","A2"…}` literal sets in favour of a single `is_valid_cefr_band` TypeGuard |
| `replay_placement.py` builds the prompt by manually constructing a `LearnerProfile` + neutral theme + `render_system_prompt` + appending `COLD_START_INSTRUCTIONS` | Replay calls a new public `render_cold_start_prompt(band)` helper from `pipeline/prompts/builder.py` so runtime and replay share one assembly path | Runtime and replay must agree byte-for-byte on the cold-start prompt for the regression bar to be meaningful; a hand-rolled assembly in the script would silently drift. Side-effect: surfaced and fixed the latent bug above (replay had been passing `fixture.true_band`; runtime always uses `default_learner_band` on fresh DB) |
| `LevelingService._fetch_recent_session_bands` uses two queries (sessions then turns IN sessions) | One CTE+JOIN query | The two-query form was N+1-shaped on a path that runs every end-of-session. Single round-trip with the same result set |
| Per-turn `cefr_band` flows through `eval/agent/types.py::TurnRecord` | Threaded as `TurnRecord.cefr_band: CEFRBand | None` (passthrough); `SessionRecord.cefr_band` left untouched | `SessionRecord.cefr_band` already exists as the persona's session-level expected band — distinct from the per-turn model emission. Both coexist; do not collapse |
| Tests as listed in spec | All listed tests landed, plus a 6-case `tests/test_band_history.py` + 12-case `tests/test_placement_policy.py` + 22-case `tests/test_leveling_policy.py` | Required to validate the policy/service layers in isolation; no scope drift |

Acceptance criteria, reviewed post-implementation:

- ✅ `LOG_TURN_TOOL` gains required `cefr_band` enum with rubric-derived description.
- ✅ `TurnObservation.cefr_band` + `band_missing` sink counter; degraded path validated.
- ✅ Alembic revision adds `turns.cefr_band`, `band_history`, profile counters.
- ✅ Cold-start branch routes to the four-step diagnostic ladder.
- ✅ Placement runs at end of session 1; modal selection; abstains below 3 valid turns.
- ✅ Cold-start branch no longer taken on session 2 once placement row exists.
- ✅ `is_calibrated` derived from `band_history` placement row; threaded through `snapshot_to_profile`.
- ✅ Auto-leveling at end of every post-placement session; rolling mean over last 3 sessions.
- ✅ Pure-function policy is deterministic; property-style tested.
- ✅ Asymmetric hysteresis K=3 / K=4 + demotion crossing margin.
- ✅ `band_history` schema present; `signals` JSONB carries decision metadata.
- ✅ `GET /dev/learner` extended with `is_calibrated`, `stable_sessions_at_band`, `band_history`, `recent_turn_bands`.
- ✅ `scripts/replay_placement.py` shipped; advisory CI step.
- ⏳ `replay_placement.py` against the cold_start fixtures meets the bar — **not yet measured** against a live llama.cpp endpoint. The CI gate is advisory; the pre-merge promotion-to-blocking decision happens when the user runs the script.
- ✅ All listed pytest files exist + pass; DB tests skip cleanly without Postgres.
- ✅ Full pytest suite passes; ruff and mypy clean on the spec-049 CI scope.

---

## Spec Gaps Exposed

- **Replay-bar measurement is the open empirical question this spec
  was meant to answer.** The runtime substrate landed; the regression
  bar against the live llama.cpp endpoint has not yet been measured.
  The decision to promote the CI gate to blocking, or to open a
  follow-up fine-tune spec, depends on that one local run. Captured as
  a follow-up: "049-followup: measure replay_placement.py against live
  llama.cpp; decide blocking vs. fine-tune spec".
- **The asymmetric hysteresis "stable counter" semantics needed
  invention not in the spec.** The spec sketched
  `evaluate_leveling(stable_sessions: int)` taking a single int but did
  not pin down what the int counts when the target switches direction
  (promote-then-demote sequences) or returns to current. The
  implementation chose: counter resets on stable-at-current OR
  borderline-demote, increments on promote-target OR non-borderline
  demote-target; same counter shared across directions. This is
  deterministic and passes the spec's listed test cases, but a more
  formal "consecutive same-direction" tracking would need a second
  state field (e.g., signed int, or an extra `stable_target_band`
  column). Worth a doc pass if real-world data shows direction-flip
  edge cases.
- **`is_calibrated` semantics ripple was larger than the spec hinted.**
  Threading the new explicit parameter through `snapshot_to_profile`
  touched the eval-accumulator path (`run_agent_eval.py` defaults to
  `False`), the cold_start fixture's `expected_profile_update.is_calibrated`
  field (already authored as `True` in fixtures, no change needed), and
  one existing test
  (`tests/test_agent_personas.py::test_snapshot_to_profile_composes_with_render_system_prompt`)
  that relied on the implicit derivation. Mostly mechanical, but the
  spec's "ripple" call-out understated the surface area.
- **The pure-function module's exported API drifted from spec to
  implementation.** Spec listed `BAND_TO_FLOAT`; implementation also
  needed `BAND_MIDPOINT` (canonical) + a `BAND_TO_FLOAT` alias for
  backwards compatibility, plus `band_index` (originally `_band_index`)
  for the policy's distance comparison and the replay script's
  duplication. None of this changes behaviour; it's a naming-clarity
  pass that is captured here so future agents don't re-introduce the
  underscore-private form.

---

## Test Evidence

```
$ .venv/bin/python -m pytest tests/
======================= 289 passed, 9 warnings in 11.34s =======================

$ .venv/bin/python -m pytest tests/test_band_history.py \
    tests/test_placement_policy.py \
    tests/test_leveling_policy.py \
    tests/test_log_turn_ingestion.py
============================== 48 passed in 1.26s ==============================

$ .venv/bin/python -m ruff check hable_ya/ api/ eval/agent/ tests/ \
    scripts/voice_client.py scripts/replay_placement.py
All checks passed!

$ .venv/bin/python -m mypy hable_ya/ api/ eval/agent/ \
    tests/test_tool_handler.py tests/test_runner.py tests/test_health.py \
    tests/test_prompts.py tests/test_db.py tests/test_init_db.py \
    tests/conftest.py tests/test_agent_personas.py tests/test_agent_cache.py \
    tests/test_agent_accumulator.py tests/test_agent_learner.py \
    tests/test_agent_judge_prompts.py tests/test_agent_aggregates.py \
    tests/test_aggregations_shared.py tests/test_learner_profile.py \
    tests/test_placement_policy.py tests/test_leveling_policy.py \
    tests/test_band_history.py scripts/replay_placement.py
Success: no issues found in 73 source files
```

DB tests skip cleanly when Postgres is not reachable (preserves the
spec-028 conftest behavior):

```
$ .venv/bin/python -m pytest tests/test_band_history.py \
    tests/test_placement_policy.py tests/test_leveling_policy.py \
    tests/test_log_turn_ingestion.py
collected 48 items

tests/test_band_history.py ssssss                                        [ 12%]
tests/test_placement_policy.py ............                              [ 37%]
tests/test_leveling_policy.py ......................                     [ 83%]
tests/test_log_turn_ingestion.py ssssssss                                [100%]

======================== 34 passed, 14 skipped in 0.20s ========================
```

Manual / live verification owed (per the spec's Confidence section,
explicitly out of scope for this automated landing):

- **Live `replay_placement.py` run** against the local llama.cpp
  endpoint over the 25 cold_start fixtures. Reports per-fixture rows,
  per-band aggregates, 5×5 confusion matrix, and overall
  accuracy + MAE. Outcome decides whether the CI gate is promoted to
  blocking in this slice's PR or stays advisory until a follow-up
  fine-tune lands.
- **One live conversation against the rewritten
  `COLD_START_INSTRUCTIONS`**: speak Spanish through the four-step
  ladder; disconnect; verify via `psql` that `turns.cefr_band` was
  populated per turn, `learner_profile.band` was set by placement, and
  `band_history` has the `placement` row.
- **Three above-band sessions**: speak deliberately above the placed
  band for three consecutive sessions; verify `band_history` has an
  `auto_promote` row at session 4 (placement + K=3) and not earlier.
