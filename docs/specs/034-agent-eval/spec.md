# Spec: Agent Eval — Synthetic Learner, Opus Session Judge, Orchestrator

| Field | Value |
|---|---|
| id | 034 |
| status | approved |
| created | 2026-04-24 |
| covers roadmap | #034, #035, #036 (bundle) |

---

## Why

`eval/run_eval.py` scores the model one turn at a time against hand-authored
fixtures. It is the right tool for measuring whether the fine-tuned Gemma can
produce a well-formed recast on any given prompt. It is the wrong tool for
measuring what happens when the assembled system — fine-tuned model +
per-band system prompt + `log_turn` emission + live learner profile + theme
rotation — drives a full session. Turn-level scores can be green while the
agent still produces sessions that drift off-topic, push the learner out of
their band by the third turn, recast mechanically in isolation but never in
running conversation, dominate the conversation instead of leaving
production space, or fail to adapt after the learner repeats the same error
four times.

Agent eval closes that gap. A simulated learner drives multi-turn sessions
against the fine-tuned model through the same system-prompt renderer the
runtime uses; an Opus judge reads each transcript and scores it on five
session-level dimensions; the orchestrator aggregates per-scenario, per-band,
per-dimension and emits a JSON report compatible with the existing
baseline-vs-tuned comparator shape. The instrument is the eval loop that
catches regressions in the *composition* of everything 021, 023, and 029
built — the parts whose interactions turn-level eval cannot see.

### Consumer Impact

- **Project owner (researcher/developer):** gets a session-level regression
  harness. Can answer "did my prompt tweak / fine-tune / learner-model
  change make sessions better or worse?" without subjective 5-minute live
  conversations. Can run agent eval against a candidate checkpoint before
  merging, and against any prompt-builder change before shipping. Judgments
  are cached by transcript hash so re-runs over the same (model, persona)
  pair are free.
- **Downstream model work:** the 5-dim session scores plus per-dim rationales
  become the triage signal when a turn-level metric regresses — a drop in
  `recast_present` can now be correlated with a drop in `recast_naturalness`
  or `learner_production_space`, and the judge's one-sentence rationales
  point at which scenario broke. `eval/compare.py`'s existing
  threshold-driven recommendation pattern extends to session metrics with
  minimal new machinery.
- **End user (learner):** no direct surface. The agent's behavior improves
  only insofar as agent-eval signal changes which checkpoints ship.

### Roadmap Fit

Bundles three planned items:

- **#034** Synthetic learner simulator with error patterns.
- **#035** Opus session-outcome judge across five dimensions: pedagogical
  flow, level consistency, recast naturalness, learner production space,
  coherence.
- **#036** Agent-eval orchestrator (end-to-end session runs).

Bundling rationale: #034 produces the transcripts, #035 consumes them, #036
wires both into a runnable command and a report. Splitting produces three
PRs whose intermediate states are non-functional (a learner with no judge, a
judge with no input, an orchestrator with nothing to orchestrate). Follows
the bundling precedent of specs 023 and 029. The three stub files under
`eval/agent/` (`synthetic_learner.py`, `opus_judge.py`, `run_agent_eval.py`)
already anticipate this split — the spec fills them in.

Dependencies:

- Upstream (required): #021 / #023 (agent loop + per-band prompt renderer
  shared with `finetune/format.py`), #029 (learner profile so the prompt
  builder reads real state). All implemented.
- Soft dependency on #028 (Postgres + AGE): only if the spec chooses the
  real-DB profile-evolution path — see Open Questions.
- Downstream (unblocked by this): any future model change can be gated on
  agent-eval delta. #049 (auto-level) and #050 (placement calibration) will
  want agent-eval signal to justify their behavior changes.

Explicitly **not** bundled here:

- **#042** (artifact registry) — agent-eval results are written to JSON
  alongside `eval/run_eval.py` outputs; #042 will link them to checkpoints
  later.
- **#047 / #048** (STT/TTS) — agent eval is text-only; audio quality is out
  of scope.

---

## What

### Acceptance Criteria

With llama.cpp running and `ANTHROPIC_API_KEY` set:

- [ ] `python -m eval.agent.run_agent_eval --base-url http://localhost:8080
      --output agent_results.json` runs end-to-end, completing one session per
      authored persona (15 personas, 3 per band across A1/A2/B1/B2/C1) and
      emitting a single JSON file.
- [ ] A persona is defined in `eval/agent/personas/*.json`. Each persona
      specifies: `id`, `cefr_band` (A1–C1), `scenario_domain` (one of
      `THEMES_BY_LEVEL[band]`), `error_patterns` (list of categories the
      learner will repeat, e.g. `["ser_estar", "preterito_vs_imperfecto"]`),
      `L1_reliance` (float 0–1 controlling English fallback rate),
      `fluency_signal` (`weak|moderate|strong`), `turn_budget` (default 12),
      and `opening_utterance` (optional seed so the session starts
      deterministically).
- [ ] A session consists of ≤ `turn_budget` alternating (learner, agent)
      turns. The learner side is an Opus call that receives a persona-derived
      system prompt and the session transcript so far; the agent side is a
      call to llama.cpp against the same system prompt the runtime builds,
      using the persona's band + scenario. The session terminates when the
      turn budget is reached or the judge's stop signal fires (see Open
      Questions).
- [ ] After each agent turn, the learner's `log_turn` output (parsed via
      `eval.scoring.turn.parse_tool_calls`) is fed into an in-process
      profile accumulator that mirrors the aggregations in
      `LearnerProfileRepo.get()` (rolling L1_reliance + fluency mean over
      the last 20 turns, top-3 error categories, top-5 recent vocab lemmas
      via spaCy). The next agent turn's system prompt reflects the updated
      profile — this is the "does the agent adapt?" exercise.
- [ ] After the session completes, the full transcript + persona spec is
      passed to `eval.agent.opus_judge.judge_session`, which returns a
      `SessionVerdict` (Pydantic):
      - `pedagogical_flow: int` (1–5)
      - `level_consistency: int` (1–5)
      - `recast_naturalness: int` (1–5)
      - `learner_production_space: int` (1–5)
      - `coherence: int` (1–5)
      - `overall: float` (mean of the five)
      - `rationale: dict[dim, str]` (one-sentence reason per dim)
      - `stop_reason: Literal["budget_reached", "agent_derailed",
        "learner_abandoned"]`
- [ ] Judge calls are cached on disk keyed by `sha256(transcript +
      persona_id + judge_system_prompt_version)`. Re-running `run_agent_eval`
      without changing the model or persona set incurs no Anthropic spend
      for the judge.
- [ ] The orchestrator emits `agent_results.json` with: per-session entries
      (persona_id, transcript, verdict, model_label, elapsed_s), and
      aggregates shaped after `eval/run_eval.py::compute_aggregates`:
      overall, by_dimension, by_band, by_persona_error_pattern.
- [ ] `python -m eval.agent.compare baseline_agent.json tuned_agent.json`
      prints per-dimension, per-band, per-error-pattern deltas. The command
      matches the look-and-feel of `eval.compare` (rich table), and uses
      the same threshold shape (per-dim floor + recommendation) — exact
      thresholds deferred to a post-baseline calibration pass (see Open
      Questions).
- [ ] A `--personas` flag filters by persona id pattern (glob or
      comma-separated list) for dev iteration; a `--limit N` flag caps total
      sessions; a `--dry-run` flag renders persona → transcript-request
      pairs without hitting either endpoint so prompt authoring is testable
      offline.
- [ ] A `--minimal-prompt` flag mirrors `eval.run_eval.MINIMAL_SYSTEM_PROMPT`
      so the agent-eval baseline "untuned + unprompted" mode is measurable.
- [ ] Unit tests (see Testing Approach) all pass. Tests requiring Anthropic
      skip cleanly when `ANTHROPIC_API_KEY` is unset (same posture as the
      existing `eval.judge_recasts` tests).
- [ ] `ruff` and `mypy` pass on `eval/agent/**`. The CI scope in `.github/
      workflows/ci.yml` is extended to include `eval/agent/` (currently
      unscoped because of pre-existing lint debt in `eval/`).

### Non-Goals

- **No Pipecat, no WebSocket, no audio.** Agent eval talks directly to
  llama.cpp's OpenAI-compatible endpoint, the same way `eval/run_eval.py`
  does. Pipecat's frame dispatching, STT, and TTS are out of scope;
  regressions there are audible to humans but not measurable here. (If a
  future spec wants to cover them, it uses a Pipecat test transport — not
  this spec.)
- **No real Postgres dependency.** The profile accumulator is in-process
  and mirrors the 029 aggregation logic. Agent eval does not require a
  running Postgres. See Key Decisions for the duplication trade-off and
  Open Questions for whether to revisit.
- **No writing to the production learner DB.** Agent-eval sessions are
  ephemeral; nothing they emit is persisted to `turns`,
  `error_observations`, `vocabulary_items`, or the AGE graph.
- **No automatic threshold calibration.** The spec establishes the 5-dim
  score shape; what counts as "ship" vs. "fine-tune-more" is calibrated
  by running agent eval against the current checkpoint after merge and
  picking thresholds from the observed distribution. That is a follow-up
  pass, not a spec goal.
- **No learner-voice generation with speech.** Learner utterances are
  text only. Realism of the simulator is bounded by what Opus can produce
  in text form.
- **No multi-session memory.** Each session starts from a persona's
  declared profile. Persona-driven "learner has done 3 sessions already"
  is authored into the persona, not derived from chaining sessions.
  (Multi-session continuity is a future spec if ever.)
- **No model-vs-model play.** The learner is always Opus, the agent is
  always the served llama.cpp checkpoint. Swapping learner models is
  possible via CLI flag but not a v1 feature.
- **No replacement of turn-level eval.** `eval/run_eval.py` stays
  authoritative for turn-level metrics; agent eval is additive.
- **No frontend / dashboard.** Results are JSON + rich-table CLI output.
- **No automatic persona generation.** The ≥ 15 personas are hand-authored
  under `eval/agent/personas/*.json`. A future spec can let Opus draft
  persona candidates, but this spec commits the initial set by hand so the
  signal is stable and the authored failure modes are what the agent is
  measured against.

### Open Questions

1. ~~**In-memory profile accumulator vs. real Postgres round-trip.**~~
   **Resolved: in-memory, with the aggregation core lifted into a shared
   pure function.** A new `hable_ya/learner/aggregations.py` exposes
   `compute_snapshot(turns, *, band, sessions_completed, window_turns,
   top_errors, top_vocab) -> LearnerProfileSnapshot`. Both
   `LearnerProfileRepo.get` (refactored) and the agent-eval
   `ProfileAccumulator` call it. Prevents silent drift between eval and
   production; no Postgres dependency in the agent-eval path. See Key
   Decisions #4 and Approach §3.

2. ~~**Scripted learner vs. LLM-driven learner.**~~ **Resolved:
   LLM-driven (Opus), with per-utterance disk cache keyed by
   `sha256(persona.id + canonicalized_transcript)`.** First run generates
   learner turns live; subsequent runs with the same (model, persona)
   pair replay from cache as long as the agent's utterances don't change.
   The cache key incorporates the full prior transcript so any agent-side
   change invalidates downstream cached learner turns — cache hits cannot
   hide agent regressions. See Approach §2.

3. ~~**Judge stop conditions.**~~ **Resolved: post-hoc only. Sessions
   always run to `turn_budget`; `stop_reason` is a label the judge
   applies after reading the full transcript.** No mid-session
   interrupts, no magic tokens, no side classifier. Pathological sessions
   waste at most 12 turns of compute — acceptable. See Key Decisions #10.

4. ~~**Threshold calibration for `eval.agent.compare`.**~~ **Resolved:
   ship with placeholder thresholds of `3.5` across all five dimensions
   plus `overall ≥ 3.5`, each TODO-marked.** Real thresholds are picked
   from the distribution of the first baseline run. `compare.py` prints
   the recommendation column from day one but the numbers are explicitly
   provisional until a post-baseline recalibration pass.

5. ~~**Scenario coverage matrix.**~~ **Resolved: 15 personas, distributed
   3 per band across A1/A2/B1/B2/C1.** Each persona tests a distinct
   1–2 error-pattern combination drawn from categories the fine-tune
   targets (`single_error_recast`, `multi_error`, `tool_call_correctness`).
   Concrete error-pattern assignments are made during persona authoring
   — the spec commits the 3-per-band count but not the specific
   error/scenario pairings (author judgment drives that).

6. ~~**Cost cap per run.**~~ **Resolved: first-run budget ~$4–$8;
   subsequent runs ~$0 unless the agent output changes.** Concrete
   estimate below. The orchestrator logs both an estimated cost before
   sessions start and an observed cost after, so drift is visible.

   **First-run cost model (Opus 4.7 pricing: $15/Mtok input, $75/Mtok
   output):**

   | Component | Calls | Avg input tok | Avg output tok | Subtotal |
   |---|---|---|---|---|
   | Learner turns | 15 personas × 12 turns = 180 | ~800 | ~80 | $2.16 in + $1.08 out = **$3.24** |
   | Judge verdicts | 15 sessions × 1 | ~1500 | ~500 | $0.34 in + $0.56 out = **$0.90** |
   | Judge system-prompt cache read | 14 cached (first call uncached) | — | — | ~$0.05 savings, negligible |
   | **Total (first full run)** | | | | **~$4.15** |

   Assumptions: learner input grows with transcript (last call sees
   ~2000 input tokens; average ~800 across the 12 turns), learner output
   ~80 tokens per turn (short Spanish utterances with English fallback).
   Judge input is the full 12-turn transcript plus persona spec
   (~1500 tok); output is 5 scores + rationales (~500 tok).

   **Subsequent runs (same model, same personas):**
   - Learner cache hit rate → ~100%; learner cost → ~$0.
   - Judge cache hit rate → ~100%; judge cost → ~$0.
   - Total ≈ **$0.00** (only llama.cpp inference, which is free locally).

   **Runs after an agent-side change (e.g. new checkpoint):**
   - Learner turns downstream of the first divergent agent utterance
     re-roll; average ~50–80% re-roll rate per session.
   - Judge fully re-rolls (transcript hash changed).
   - Total ≈ **$2–$4** per run.

   **Budget guard.** The orchestrator prints the estimate above before
   any Opus calls fire, including a breakdown by (cached vs. uncached)
   learner turns based on the existing cache state on disk. If actual
   spend exceeds the estimate by more than 50%, the orchestrator logs a
   warning at the end of the run so unexpected drift is visible.

---

## How

### Approach

Five concerns, in implementation order:

#### 1. Persona format + authoring

`eval/agent/personas/*.json`, one file per persona. Schema:

```python
# eval/agent/personas/schema.py
class Persona(BaseModel):
    id: str                                    # e.g. "a2_ser_estar_coffee"
    cefr_band: CEFRBand
    scenario_domain: str                       # must match THEMES_BY_LEVEL[band]
    error_patterns: list[str]                  # categories the learner repeats
    L1_reliance: float = Field(ge=0, le=1)     # informs Opus system prompt
    fluency_signal: FluencySignal
    turn_budget: int = 12
    opening_utterance: str | None = None       # optional deterministic seed
    notes: str | None = None                   # author note; not sent to LLMs
```

15 personas authored as 3-per-band across A1/A2/B1/B2/C1, biased toward
categories the fine-tune actually targets (`single_error_recast`,
`multi_error`, `tool_call_correctness`). The spec commits the count and
per-band distribution; the specific scenario × error-pattern pairings
are author judgment during implementation.

A loader (`load_personas(path: Path) -> list[Persona]`) validates every
JSON in the directory on startup; `scenario_domain` is cross-checked
against `THEMES_BY_LEVEL[persona.cefr_band]` and fails fast on mismatch.

#### 2. Synthetic learner (`eval/agent/synthetic_learner.py`)

```python
class SyntheticLearner:
    def __init__(
        self,
        persona: Persona,
        client: anthropic.AsyncAnthropic,
        model: str = "claude-opus-4-7",
    ) -> None: ...

    async def next_utterance(
        self,
        transcript: list[ConversationTurn],
    ) -> str: ...
```

The learner system prompt is built from the persona: "You are playing a
Spanish language learner at CEFR band B1. You are talking with a tutor.
Your goal is to discuss {scenario}. You naturally make these kinds of
errors: {error_patterns with short examples}. You fall back to English
when you don't know a word ~{L1_reliance * 100}% of the time. Your
speech is {fluency_signal}: {short | moderate | flowing} sentences.
Produce ONE learner turn. Return only the Spanish (with English fallback
where natural), nothing else."

The first call uses `persona.opening_utterance` if set (deterministic
seed); otherwise asks Opus for the opening turn. Subsequent calls pass
the running transcript so the learner stays coherent with its earlier
utterances.

**Learner turn caching.** Keyed by `sha256(persona.id +
transcript_canonicalized)`. A cache hit returns the prior utterance
without an API call. This is what makes "replay the same session
deterministically" cheap — as long as the agent's utterances don't change,
the learner's responses don't re-roll.

#### 3. In-memory profile accumulator (`eval/agent/accumulator.py`)

Mirrors the aggregation logic in `hable_ya/learner/profile.py::LearnerProfileRepo.get`
without the DB. The aggregation core is lifted into a shared pure
function so eval and production compute identical snapshots:

```python
# New: hable_ya/learner/aggregations.py
def compute_snapshot(
    turns: list[TurnRecord],          # last N turns, oldest-first
    *,
    band: CEFRBand,
    sessions_completed: int,
    window_turns: int,
    top_errors: int,
    top_vocab: int,
) -> LearnerProfileSnapshot: ...
```

Both `LearnerProfileRepo.get` (after refactor) and the agent-eval
accumulator call into this. The accumulator maintains an ordered list of
`TurnRecord` tuples parsed from `log_turn` calls the fine-tuned model
emits during the agent-eval session, and re-calls `compute_snapshot`
before rendering the next agent turn's system prompt. Same content-word
lemma extraction via `eval.scoring.recast.content_tokens` (already
exposed per 029 decision).

If `log_turn` is missing from an agent turn (per project memory, ~20%
miss rate on deployed Gemma), the accumulator skips that turn's
contribution. This is faithful to production: the graceful-degradation
posture is the same.

#### 4. Agent caller (reuse of `eval.run_eval.call_model` shape)

A slim wrapper that:

1. Builds the system prompt via `finetune.format.render_system_prompt(
   SystemParams(profile=snapshot_to_profile(current_snapshot),
   theme=THEMES_BY_LEVEL[band][...matching scenario_domain]),
   band=band)` — the *same* renderer the runtime uses.
2. Sends `[system, *transcript]` to llama.cpp's `/v1/chat/completions`,
   with the same Gemma-specific extras (`chat_template_kwargs={
   "enable_thinking": False}` when `--no-thinking` is set; `temperature=0.0`
   by default for reproducibility).
3. Returns the raw response text for parsing by the accumulator.

The `snapshot_to_profile` mapper is the same one `build_session_prompt`
uses; it's lifted into `hable_ya/learner/profile.py` (or a sibling module)
so both consumers share it. This is the one production-code touch this
spec makes outside of the shared-aggregation refactor.

#### 5. Opus judge (`eval/agent/opus_judge.py`)

```python
class SessionVerdict(BaseModel):
    pedagogical_flow: int = Field(ge=1, le=5)
    level_consistency: int = Field(ge=1, le=5)
    recast_naturalness: int = Field(ge=1, le=5)
    learner_production_space: int = Field(ge=1, le=5)
    coherence: int = Field(ge=1, le=5)
    overall: float
    rationale: dict[str, str]
    stop_reason: Literal["budget_reached", "agent_derailed", "learner_abandoned"]


async def judge_session(
    client: anthropic.AsyncAnthropic,
    persona: Persona,
    transcript: list[ConversationTurn],
    *,
    cache_dir: Path,
) -> SessionVerdict: ...
```

Judge system prompt is stable (cached via Anthropic's ephemeral cache like
`eval/judge_recasts.py`) and versioned by a constant
`JUDGE_SYSTEM_VERSION = "1"`. Version bump = cache bust. Five rubric
blocks define what 1/3/5 looks like per dimension, drawn from the
pedagogical thresholds in `eval/compare.py` and the recast patterns in
`finetune/format.py`:

- **pedagogical_flow** — does the conversation progress, or does the
  agent repeat itself / force awkward topic changes?
- **level_consistency** — is the agent's Spanish actually at the persona's
  declared band throughout, or does it drift up/down?
- **recast_naturalness** — when the learner errs, does the correction
  feel like natural conversation, or is it a correction-with-extra-steps?
- **learner_production_space** — how much of the conversation is
  learner-produced vs. agent-produced? The agent should be eliciting,
  not lecturing.
- **coherence** — does the session stay on the scenario topic and does
  the agent remember what the learner has said turn-over-turn?

The judge returns JSON via `anthropic.messages.parse(output_format=
SessionVerdict)` (same pattern as `eval/judge_recasts.py`).

**Cache.** `cache_dir/{sha256}.json`. Key is
`sha256(JUDGE_SYSTEM_VERSION + persona.id + canonicalized_transcript)`.
Hit → load; miss → call + write.

#### 6. Orchestrator (`eval/agent/run_agent_eval.py`)

```python
async def run_agent_eval(args: Namespace) -> AgentEvalOutput:
    personas = load_personas(args.personas_dir)
    if args.personas:
        personas = filter_by_glob(personas, args.personas)
    if args.limit:
        personas = personas[: args.limit]

    llama_client = openai.AsyncOpenAI(base_url=..., api_key="not-needed")
    anthropic_client = anthropic.AsyncAnthropic()

    sessions: list[SessionRecord] = []
    semaphore = asyncio.Semaphore(args.concurrency)

    async def run_one(p: Persona) -> None:
        async with semaphore:
            transcript, snapshot_trace = await simulate_session(
                persona=p,
                learner=SyntheticLearner(p, anthropic_client),
                agent_caller=agent_caller,
                accumulator=ProfileAccumulator(p),
                max_turns=p.turn_budget,
                minimal_prompt=args.minimal_prompt,
                no_thinking=args.no_thinking,
            )
            verdict = await judge_session(
                anthropic_client, p, transcript, cache_dir=args.cache_dir,
            )
            sessions.append(SessionRecord(
                persona_id=p.id, transcript=transcript,
                verdict=verdict, snapshot_trace=snapshot_trace,
                model_label=args.model_label,
            ))

    await asyncio.gather(*(run_one(p) for p in personas))

    aggregates = compute_agent_aggregates(sessions)
    output = AgentEvalOutput(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(UTC).isoformat(),
        base_url=args.base_url,
        session_count=len(sessions),
        model_label=args.model_label,
        sessions=sessions,
        aggregates=aggregates,
    )
    Path(args.output).write_text(output.model_dump_json(indent=2))
    return output
```

`compute_agent_aggregates` is a near-clone of `eval.run_eval.compute_aggregates`
shaped for the 5 session dimensions plus `overall`, grouped by `band`
(from persona.cefr_band) and by `error_pattern` (each persona.error_patterns
entry contributes a row).

A cost-preview log fires before sessions start, drawing on the cost
model in Open Question #6:

```
Estimated Opus spend (15 personas × 12 turns):
  Learner turns:  180 calls — 132 uncached, 48 cached → ~$2.38
  Judge:          15 calls — 11 uncached, 4 cached → ~$0.66
  Total estimate: ~$3.04
  (subsequent runs over the same model+personas: ~$0.00)
```

Cache state is read off disk at startup so the estimate accounts for
prior runs. After the run, the orchestrator logs observed spend (from
Anthropic response usage fields) and warns if observed > 1.5×
estimated.

Concurrency default = 4 (matches `run_eval.py`). Per-session timeout =
120 s.

#### 7. Comparison (`eval/agent/compare.py`)

Mirrors `eval/compare.py` shape:

- Load two agent-eval JSONs.
- Per-dim table (rows = dimension, columns = baseline / tuned / delta /
  recommendation).
- Per-band table (rows = band, columns = overall baseline / tuned / delta).
- Per-error-pattern table.
- Threshold constants at the top of the file (placeholder per Open
  Question #4; TODO-marked for recalibration).

#### 8. File layout

```
eval/agent/
├── __init__.py
├── personas/
│   ├── schema.py                    # Persona Pydantic model + loader
│   └── *.json                       # ≥ 15 authored personas
├── synthetic_learner.py             # SyntheticLearner + learner-utterance cache
├── opus_judge.py                    # judge_session + SessionVerdict + judge cache
├── accumulator.py                   # ProfileAccumulator (in-memory profile evolution)
├── run_agent_eval.py                # orchestrator CLI
├── compare.py                       # baseline-vs-tuned comparator
└── types.py                         # SessionRecord, AgentEvalOutput, ConversationTurn reuse

hable_ya/learner/
├── aggregations.py                  # NEW: compute_snapshot pure fn (shared by 029 repo + this accumulator)
└── profile.py                       # refactored to call aggregations.compute_snapshot
```

### Confidence

**Level:** Medium

**Rationale:**

The mechanical parts are well-understood. Calling llama.cpp's
OpenAI-compatible endpoint in a loop is what `eval/run_eval.py` already
does. Calling Opus with structured output + disk cache is what
`eval/judge_recasts.py` already does. Pydantic schemas, Rich tables,
and the CLI surface all have direct precedent in the repo. The
aggregation refactor is surgical: one pure function, two call sites.

The unknowns that bring this to Medium rather than High:

1. **Opus-as-learner persona stability.** A persona spec that says
   "make ser/estar errors at band B1" depends on Opus reliably producing
   those errors without over-steering. If the learner ends up too
   well-behaved (Opus's instinct is to write correct Spanish), the
   agent's recast behavior goes untested. Mitigation: few-shot examples
   in the learner system prompt drawn from `eval/fixtures/single_error_recast.json`
   fixture utterances. Validation requires seeing a first run.

2. **Judge score calibration.** The 5-dim rubrics are new. What Opus
   calls "3/5 recast naturalness" on the first run is not a priori
   correlated with researcher judgment. Scores will be usable for
   *deltas* (baseline vs. tuned) before they're usable as absolute
   quality signals. The placeholder thresholds in `compare.py` are
   explicit about this.

3. **Aggregation-refactor blast radius.** Lifting `compute_snapshot` out
   of `LearnerProfileRepo` touches 029's test surface. The existing
   `tests/test_learner_profile.py` must be re-pointed at the pure
   function; the repo test should become a thin assertion that the repo
   loads turn rows + delegates. Low risk if done mechanically.

**Validate before proceeding:**

1. **Author 3 personas and run a single smoke session end-to-end before
   committing to 15.** Confirms persona format is workable, learner
   utterances are realistically erroneous, judge output parses cleanly,
   and wall-clock per session is in the 30–60s range. ~2 hours. If the
   learner behavior is off, persona schema gets an `few_shot_examples`
   field before scaling to 15.
2. **Spike the `compute_snapshot` refactor against 029's test suite.**
   Move the aggregation core, re-point the existing tests, confirm all
   green. Done before any agent-eval code is written. ~30 min.

### Key Decisions

1. **Bundle #034 + #035 + #036 as one spec.** Same rationale as 023 and
   029 — splitting produces non-functional intermediates.
2. **Talk to llama.cpp directly; no Pipecat, no WS.** The unit of eval is
   the model's *conversational composition* given the runtime's system
   prompt. Pipecat integration is orthogonal and belongs to a future
   spec if it's ever needed (Pipecat has its own test transports).
3. **Opus-driven learner, not scripted.** Scripted learners can't be
   derailed or adapt, which means the "does the agent respond to
   learner's changing error pattern over turns?" question is unanswerable.
   Caching keeps the signal reproducible run-over-run when the agent
   output doesn't change. Trade: non-determinism on the first run for a
   given (model, persona) pair; stable thereafter.
4. **In-memory profile accumulator, sharing aggregation core with 029.**
   No Postgres dependency; no test-DB setup per agent-eval run. Refactor
   the aggregation function out of `LearnerProfileRepo.get` into
   `hable_ya/learner/aggregations.compute_snapshot` so both the repo and
   the accumulator call the same code. Prevents silent drift.
5. **Disk cache for both learner utterances and judge verdicts.** Keyed
   by `sha256(…)` over canonical inputs + prompt-version strings.
   Anthropic cost is dominated by first-run; incremental runs are cheap.
6. **5-dim judge, integer 1–5 per dim, `overall` = mean.** Matches the
   dimensions enumerated in roadmap #035 verbatim. Integer scale is
   tractable for Opus; floats per dim add false precision.
7. **Placeholder thresholds in `compare.py`.** First baseline run
   determines real thresholds. Ship with `3.5` across the board and a
   TODO.
8. **One `agent_results.json` per run, parallel to `eval/run_eval.py`'s
   output shape.** The two eval reports live side-by-side; `#042`
   (future artifact registry) will link both to a checkpoint.
9. **Graceful `log_turn` degradation.** Missing `log_turn` on an agent
   turn skips the accumulator update for that turn. Same posture as
   production (sink `missing` counter). Agent eval should not punish
   `log_turn` flakiness at the profile-evolution level — it punishes it
   at the judge level (the judge sees the transcript and can penalize
   lack of adaptation).
10. **No mid-session interrupts.** Sessions always run to budget; the
    judge labels them post-hoc (`stop_reason`). Simpler; small wasted
    compute on derailed sessions is acceptable at ≤ 12 turns.

### Testing Approach

The existing pytest suite's `ANTHROPIC_API_KEY` gate (same as
`eval.judge_recasts` tests) is the skip mechanism for anything that calls
Opus. llama.cpp tests are similarly guarded (`LLAMA_CPP_URL` reachable).

**Unit tests (no network):**

- `tests/test_agent_personas.py`:
    - Loader parses a directory of well-formed JSON files.
    - Invalid `scenario_domain` (not in `THEMES_BY_LEVEL[band]`) fails
      validation.
    - Unknown `error_patterns` category fails validation.
    - Every authored persona in `eval/agent/personas/` loads.

- `tests/test_agent_accumulator.py`:
    - `ProfileAccumulator` initializes from a `Persona` with neutral
      defaults.
    - Feeding a sequence of synthetic `log_turn` records (with known
      `L1_used`, `fluency_signal`, `errors`, utterance lemmas) produces
      the expected `LearnerProfileSnapshot` after each turn.
    - Equivalence with `LearnerProfileRepo.get` on the same turn sequence
      — the shared `compute_snapshot` fn is called by both and must
      return identical output given identical input.
    - Missing-`log_turn` turns are skipped (accumulator state unchanged).

- `tests/test_agent_judge_prompts.py`:
    - Judge system prompt rendering is stable (byte-identity per
      `JUDGE_SYSTEM_VERSION`).
    - Verdict JSON parses into `SessionVerdict` for a hand-authored
      example; bounds validation (1–5) fires on out-of-range input.

- `tests/test_agent_cache.py`:
    - Learner-utterance cache hit returns stored value without invoking
      the client (use a mock that fails if called).
    - Judge cache ditto.
    - Cache key stability: same input → same key; transcript perturbation
      → different key.

- `tests/test_agent_aggregates.py`:
    - `compute_agent_aggregates` over a hand-built list of `SessionRecord`
      produces the expected by_dim / by_band / by_error_pattern shapes.

- `tests/test_aggregations_shared.py` (new; covers the refactor):
    - `compute_snapshot` returns neutral defaults on empty turn list.
    - Known 5-turn sequence produces the expected rolling means and
      top-N lists.
    - `LearnerProfileRepo.get` output matches `compute_snapshot` when
      called with the same turn rows (integration against test DB).

**Smoke tests (network; skip cleanly if keys missing):**

- `tests/test_agent_judge_smoke.py`:
    - One canned transcript + persona → live Opus call → a `SessionVerdict`
      returned, scores in 1–5. No assertion on score values (non-det);
      only schema + bounds.

- `tests/test_agent_learner_smoke.py`:
    - One persona → `SyntheticLearner.next_utterance([]) ` returns a
      non-empty Spanish string. Assertion is structural, not content.

**Manual validation (out of pytest):**

- Run `python -m eval.agent.run_agent_eval --base-url http://localhost:8080
  --output baseline_agent.json --limit 3` against the current fine-tuned
  GGUF. Spot-check:
    - The three transcripts are coherent Spanish conversations.
    - The agent's utterances are at the persona's band.
    - The judge rationales are plausible and map to what's actually in
      the transcript.
    - The cost-preview log line matched the post-run cost within ~30%.
- Run again without changing anything. Confirm zero Anthropic calls
  (both caches fully warm).
- Run with a minimal prompt flag (`--minimal-prompt`). Confirm the
  scores drop measurably — this is the sanity check that the eval is
  actually measuring prompt/fine-tune composition.
- `python -m eval.agent.compare baseline_agent.json baseline_agent.json`
  — trivial identity diff; all deltas should be zero.

---

This is the slice that turns regression-catching on the agent from "run
a 5-minute Spanish conversation and see how it feels" into "commit the
change, run agent eval, inspect the delta table." Its job is to stand up
the session-level instrument that turn-level eval can't replace and
that live conversation can't replace at the cadence of iteration.
