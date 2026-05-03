# Spec: Learner Leveling — Initial Placement + Auto-Update via Model-Emitted Per-Turn CEFR Band

| Field | Value |
|---|---|
| id | 049 |
| status | approved |
| created | 2026-04-25 |
| covers roadmap | #049, #050 (bundle) |

---

## Why

Spec 029 made the learner profile real: every `log_turn` writes through to
`turns`, `error_counts`, `vocabulary_items`, and the AGE graph; the prompt
builder reads back rolling-window aggregates. The piece 029 punted on is
**the band itself**. `learner_profile.band` is bootstrapped to `'A2'` in
migration `bd55d203ae25_learner_model.py:103` and never changes.
`LearnerProfileRepo.set_band` exists for #049 to consume but nothing in the
runtime calls it.

The original product framing (OVERVIEW.md) gave the model itself the job of
pedagogical assessor. Today's `log_turn` schema (`hable_ya/tools/schema.py`)
operationalizes that for fluency (`fluency_signal: weak|moderate|strong`),
L1 reliance (`L1_used: bool`), and error patterns (`errors[]`), but it
stops short of the assessor's primary output: the learner's CEFR band on
this turn. Without that field the runtime has no model-derived band signal
at all — placement and auto-leveling either have to reverse-engineer a
band from the secondary signals, or the band stays static at config
default forever.

This slice closes the band loop end-to-end by making the **model the
assessor of record** and threading its assessment into placement +
leveling:

1. **`log_turn` schema gets a `cefr_band` field.** The model emits an
   A1–C1 classification of the learner's last utterance on every turn.
   Persisted on `turns.cefr_band`. No fine-tune in this slice — landed
   purely via prompt engineering on the existing fine-tuned Gemma. If
   prompt engineering can't get the model to a regression bar
   (`accuracy ≥ 0.75`, `MAE ≤ 0.20` against the 25 labeled `cold_start`
   fixtures), a follow-up spec adds a fine-tune pass on the field; this
   spec stops at "find out".
2. **Placement (#050).** Session 1 runs as a four-step diagnostic ladder
   (introduction → daily life → past → opinion). At end-of-session,
   placement takes the modal `cefr_band` across the diagnostic turns and
   calls `set_band`. `is_calibrated` flips true. Session 2 runs the
   regular theme-rotation prompt against the placed band.
3. **Auto-leveling (#049).** At each subsequent end-of-session, the
   leveling policy reads the rolling mean band over the last 3 sessions
   of turns (band-as-numeric: A1=0.1, A2=0.3, B1=0.5, B2=0.7, C1=0.9). If
   the mean crosses the current band's bucket boundary by margin, and
   does so for K consecutive sessions (K=3 promote, K=4 demote), the
   policy flips the band via `set_band`. Every change writes a
   `band_history` row.

The model already does the assessment work — fluency, L1, errors are all
its judgments per turn. Adding the band field is asking the model to emit
the conclusion it's already implicitly drawing, instead of reverse-
engineering it from the components.

### Consumer Impact

- **End user (learner):** Their first session is a graded diagnostic that
  ends with the agent having a real band estimate. Sessions 2+ run at the
  placed register; if their production strengthens or struggles over
  weeks, the agent's register tracks them.
- **Project owner (researcher/developer):** Has a queryable band log
  (`band_history`), a per-turn classification stored on `turns.cefr_band`
  visible alongside `fluency_signal` and the model's other emitted
  signals, and a regression CLI (`replay_placement.py`) that runs the 25
  `cold_start` fixtures through the live llama.cpp endpoint and reports
  per-turn + per-session band accuracy. That CLI is the prompt-engineering
  feedback loop: tune the prompt, run replay, see the score move. If the
  model can't be prompted into the bar, the data exists to start a
  fine-tune spec from.
- **Downstream features:** #042 (artifact registry) gets a per-checkpoint
  signal — `replay_placement.py` accuracy becomes a checkpoint metric. The
  agent-eval harness (#034–#036) can run multi-session personas where the
  band evolves; today it can only run single-session-per-persona because
  the band is static.

### Roadmap Fit

Bundles two planned items:

- **#049** Auto-updating learner level from error/vocabulary signals.
- **#050** Initial-level placement / calibration.

Bundling rationale: 049 and 050 share the same write path
(`set_band`), the same primary signal (model-emitted `cefr_band`), and the
same end-of-session lifecycle hook. Splitting yields a non-functional
intermediate. Pattern: spec 029.

Explicitly **not** bundled:

- **Fine-tuning the band signal.** Per the user's direction, prompt
  engineering first. Fine-tune is a deferred follow-up, contingent on
  `replay_placement.py` failing the bar. Captured in Non-Goals.
- **#042 (artifact registry)** — independent; consumes what this spec
  produces.

Dependencies:

- Upstream (required): #029 (profile + aggregates + `set_band` hook),
  implemented. Specifically: the `turns` table, `LearnerProfileSnapshot`,
  `LearnerProfileRepo.set_band`.
- Upstream (reference): existing 25 `cold_start` fixtures at
  `eval/fixtures/cold_start.json` (5 per band, A1–C1, with per-turn
  `band_indicators` and session-level `true_band`) — used as the
  regression test set.
- Downstream (unblocked): #042 (artifact registry), a future fine-tune
  spec if prompt engineering doesn't clear the bar.

---

## What

### Acceptance Criteria

With llama.cpp + Postgres + `api/main.py` + `web/` running on a fresh
database:

- [ ] `LOG_TURN_TOOL` in `hable_ya/tools/schema.py` gains a required
  `cefr_band` parameter (`enum: ["A1","A2","B1","B2","C1"]`) with a
  description that names the band's defining production characteristics
  (so the field is self-documenting to the model). The full prompt
  rendered by `render_system_prompt` includes a band-rubric section
  derived from CEFR descriptors (one short paragraph per band).
- [ ] `TurnObservation` (`hable_ya/runtime/observations.py:24`) gains
  `cefr_band: str | None`. The handler tolerates a missing or
  out-of-enum band by setting it to `None`, logging once at WARNING
  level, and incrementing a new sink counter `band_missing` (mirroring
  the existing `missing` / `ingest_failed` counters).
- [ ] `turns.cefr_band TEXT NULL` column added via alembic revision
  (raw `op.execute(...)` per spec-028 convention), with a CHECK
  constraint allowing NULL or one of `A1|A2|B1|B2|C1`. Population is
  best-effort: turns where the model omitted the band store NULL.
- [ ] On the very first WS connection to a fresh DB, the prompt routes
  to a rewritten `COLD_START_INSTRUCTIONS` that walks the learner
  through four prompts of escalating complexity (introduction → daily
  life → past → opinion) without scripting exact opening lines.
- [ ] After session 1 ends, the placement policy runs once: it reads
  the session's turns, takes the modal `cefr_band` across non-NULL
  values, requires at least 3 of 4 turns to have valid bands (else
  abstains), calls `LearnerProfileRepo.set_band(band)`, and inserts a
  `band_history` row with `reason='placement'`. If placement abstains,
  the user stays uncalibrated and re-enters the diagnostic on session 2.
- [ ] On the second connection, the prompt builder reads the placed
  band, the cold-start branch is no longer taken, theme-rotation kicks
  in.
- [ ] `is_calibrated` is derived from "a `band_history` row with
  `reason='placement'` exists", not from `sessions_completed > 0`.
  `snapshot_to_profile` (`hable_ya/learner/profile.py:45`) gains an
  `is_calibrated: bool` parameter; callers thread it through (default
  `False` for the eval-accumulator path which has no DB).
- [ ] At the end of every post-placement session, the auto-leveling
  policy reads the last `leveling_window_sessions=3` sessions' turns
  via a single query, computes the mean band-as-numeric, evaluates the
  promote/demote/stable rule against the current band, and either
  flips the band via `set_band` (with `band_history` row,
  `reason='auto_promote'` or `'auto_demote'`) or increments
  `learner_profile.stable_sessions_at_band`.
- [ ] The leveling policy is **deterministic**. Same input rows →
  same output decision. Property tested.
- [ ] The leveling policy applies asymmetric hysteresis: K=3 consecutive
  promote-target sessions → promote; K=4 consecutive demote-target
  sessions → demote. Single-session anomalies do not flip.
- [ ] Demotion additionally requires the rolling mean to cross at least
  one full band-width into the lower band (so a borderline session
  doesn't start the demotion clock).
- [ ] `band_history` exists as a table with `(id BIGSERIAL, from_band,
  to_band, reason, signals JSONB, changed_at)`, populated by an alembic
  revision under `hable_ya/db/alembic/versions/`. Same revision adds
  `learner_profile.stable_sessions_at_band INT NOT NULL DEFAULT 0` and
  `learner_profile.last_band_change_at TIMESTAMPTZ`.
- [ ] `GET /dev/learner` is extended with `is_calibrated`,
  `stable_sessions_at_band`, recent 5 `band_history` rows, and the most
  recent N turns' `cefr_band` values so the project owner can see what
  the model has been emitting.
- [ ] `scripts/replay_placement.py` reads
  `eval/fixtures/cold_start.json`, **runs each fixture's four learner
  utterances through the live llama.cpp endpoint** (re-using the same
  prompt rendering path the runtime uses), parses the model's emitted
  `cefr_band` per turn, computes the modal band, compares to fixture
  `true_band` and per-turn `band_indicators`. Reports both per-fixture
  rows and per-band aggregates (accuracy + MAE) plus an overall summary,
  with confusion matrix.
- [ ] `replay_placement.py` against the current cold_start fixtures
  meets `accuracy ≥ 0.75` and `MAE ≤ 0.20`. CI runs the script and
  fails the build on regression. **If the bar is not met by prompt
  engineering alone**, the spec is implemented but the regression gate
  in CI is set to advisory (`continue-on-error: true`) until a
  follow-up fine-tune spec lands. The `Confidence` section makes this
  explicit.
- [ ] `tests/test_placement_policy.py`,
  `tests/test_leveling_policy.py`, `tests/test_band_history.py`,
  extended `tests/test_dev_endpoints.py`, extended
  `tests/test_log_turn_ingestion.py` (now asserts `cefr_band` plumbing
  + placement runs at end of an uncalibrated session) exist and pass.
- [ ] Updated `tests/test_prompts.py` covers the new
  `COLD_START_INSTRUCTIONS` content + the new band-rubric section in
  the regular system prompt (byte-identity tests follow the spec-023
  pattern).
- [ ] Full pytest suite passes; DB tests skip cleanly when Postgres is
  unreachable (preserves spec-028 behavior).
- [ ] `ruff` and `mypy` pass on new + modified files. CI scope extended.

### Non-Goals

- **No fine-tuning in this slice.** Per user direction: prompt engineering
  first. The fine-tuned Gemma was trained on the 4-field `log_turn`
  schema (per-project memory, fine-tune scope is `recast_present` +
  `tool_args_correct` only). Adding `cefr_band` is a prompt-only change;
  the model is asked to emit it via the new tool description + a
  band-rubric section in the system prompt. If `replay_placement.py` falls
  below the bar, a follow-up spec adds the field to the SFT dataset and
  re-tunes. This spec stops at "validate whether prompt engineering is
  enough"; it doesn't pre-emptively fine-tune.
- **No heuristic fallback.** The original draft proposed reverse-
  engineering a band from `fluency_signal` + `L1_used` + error density +
  vocab diversity when the model didn't emit one. Dropped. If the model
  doesn't emit a valid `cefr_band` for ≥3 of the diagnostic turns,
  placement abstains and the learner stays uncalibrated; they re-enter
  the diagnostic on session 2. Cleaner failure mode, better signal
  ("did the model do its job?") for deciding whether to fine-tune.
- **No LLM-judge in the runtime path.** Replay against cold_start may
  optionally use Opus as a sanity-check (`replay_placement.py
  --opus-cross-check`) for offline calibration only. The runtime is
  on-device, no cloud LLM dependency at inference time.
- **No multi-session diagnostic.** Placement runs over the four turns of
  session 1, not session 1+2+3. Mirrors the cold_start fixtures' shape.
- **No model retraining on placement / leveling decisions.** Database
  writes only. No SFT example generation.
- **No band override UI.** Power user edits `HABLE_YA_DEFAULT_LEARNER_BAND`
  on a fresh DB or runs `psql`. No frontend.
- **No demotion below A1, no promotion above C1.** Floor and ceiling are
  hard-clamped.
- **No theme-aware leveling.** Policy reads only the rolling band mean
  + hysteresis state. AGE graph (`(:Learner)-[:ENGAGED_WITH]->(:Scenario
  {band})`) is not consulted.
- **No multi-language calibration.** Per OVERVIEW.md "Not multi-language
  at launch": the band rubric is Spanish-from-English; a Portuguese-L1
  learner whose Portuguese leaks would be misread.
- **No second tool.** `HABLE_YA_TOOLS` continues to define only
  `log_turn`. The change is to `log_turn`'s parameter set, not a new
  tool.
- **No GDPR / forget-me path.** Single-tenant; user owns the database.

### Open Questions

1. ~~**Should `cefr_band` be on every `log_turn` call, or only during the
   diagnostic?**~~ **Resolved: every call.** The auto-leveling rolling
   mean needs a stream of band signals, not just session 1's four. One
   rubric, one instruction, applies always — simpler prompt-engineering
   surface than a diagnostic-vs-regular branch. Missing-`cefr_band`
   degrades through the same posture as the existing missing-`log_turn`
   path (spec 023's `missing` counter), with a new `band_missing`
   counter alongside.

2. ~~**Where does the band-rubric content live?**~~ **Resolved: both,
   single source.** A `BAND_RUBRIC` constant in
   `hable_ya/pipeline/prompts/render.py` is rendered into (a) the
   `LOG_TURN_TOOL["function"]["parameters"]["properties"]["cefr_band"]
   ["description"]` as a short one-line-per-band gloss, and (b) the
   system prompt body as a fuller paragraph-per-band rubric. Tool
   description primes the parameter directly; system-prompt rubric is in
   the model's main context window. Single constant prevents drift.

3. ~~**How does the spec handle the `replay_placement.py` regression
   gate if prompt engineering can't reach `accuracy ≥ 0.75`?**~~
   **Resolved: ship anyway, advisory gate.** The CI gate is
   `continue-on-error: true` in this slice's landing PR. If the
   pre-merge replay run clears the bar, the gate is flipped to blocking
   in the same PR; otherwise a tracking item ("049-followup: fine-tune
   `cefr_band` field") is added to ROADMAP and the gate stays advisory
   until that follow-up lands. The substrate (schema, placement,
   leveling, audit) is correct in either regime — the merge isn't gated
   on band-emission quality; the CI signal is.

4. ~~**Should `turns.cefr_band` be backfilled from `fluency_signal`
   heuristics when NULL?**~~ **Resolved: no.** NULL means "model didn't
   emit". A backfill erases the signal that the model isn't doing its
   job, which is the exact metric we want visible to decide whether
   fine-tuning is needed.

5. ~~**How long is the rolling mean window for leveling?**~~ **Resolved:
   last 3 sessions.** Queried via `sessions ORDER BY started_at DESC
   LIMIT 3`, then `turns WHERE session_id IN (...) AND cefr_band IS NOT
   NULL`. Independent of the K=20 prompt-builder window. Sessions, not
   turns, because 1 session of an over-talker should not outweigh 3
   sessions of a quiet learner. New setting
   `leveling_window_sessions: int = 3`.

6. ~~**Asymmetric hysteresis values?**~~ **Resolved: K=3 promote, K=4
   demote, plus demotion margin.** Promotion fires after 3 consecutive
   promote-target sessions. Demotion fires after 4 consecutive
   demote-target sessions AND requires the rolling mean to cross the
   lower band's bucket center (not just the boundary). A borderline
   session doesn't start the demotion clock at all. Surfaced as
   `leveling_promote_consecutive` / `leveling_demote_consecutive`
   settings.

7. ~~**Replay output: per-fixture, per-band, or both?**~~ **Resolved:
   both.** `replay_placement.py` prints per-fixture rows (regression
   points at the specific fixtures the model got wrong), per-band
   aggregates (accuracy + MAE per `true_band`, plus overall) with the
   README's `accuracy ≥ 0.75` and `MAE ≤ 0.20` as the CI regression bar,
   plus a 5×5 confusion matrix and the `band_missing` count. Fixtures
   themselves are not changed.

8. ~~**Does `ProfileAccumulator` and `TurnRecord` need a `cefr_band`
   field for parity with the runtime?**~~ **Resolved: yes, passthrough
   only.** `TurnRecord.cefr_band: CEFRBand | None`, threaded into
   `SessionRecord` so agent-eval reports surface it. The accumulator's
   `compute_snapshot` call is unchanged — snapshots aggregate the
   prompt-builder's existing fields; the band is per-turn metadata.
   This keeps `replay_placement.py` and the agent-eval orchestrator
   sharing the same model-emission shape in their reports.

---

## How

### Approach

Six concerns to wire, in implementation order:

#### 1. `log_turn` schema + prompt extension

`hable_ya/tools/schema.py::LOG_TURN_TOOL` gains a `cefr_band` property:

```python
"cefr_band": {
    "type": "string",
    "enum": ["A1", "A2", "B1", "B2", "C1"],
    "description": (
        "Your CEFR-level read of the learner's last utterance. "
        "A1: memorized phrases, basic vocab. "
        "A2: simple sentences on familiar topics. "
        "B1: connected speech, can describe past/future. "
        "B2: detailed speech, can argue a viewpoint. "
        "C1: spontaneous, fluent, idiomatic, complex grammar."
    ),
},
```

Added to `required`. The runtime parser tolerates missing or out-of-enum
values by mapping to `None` + incrementing `band_missing`.

`hable_ya/pipeline/prompts/render.py` gains a `BAND_RUBRIC` constant — one
short paragraph per band, ~3 sentences each, focused on production
characteristics (sentence complexity, tense usage, vocabulary range,
discourse markers). `render_system_prompt` injects a `## Assessing the
learner's level` section between the existing `## Register` block and the
recast example. The same rubric is the source for the tool description's
short gloss above; both render from `BAND_RUBRIC`.

#### 2. Database schema (alembic revision)

A new revision under `hable_ya/db/alembic/versions/<rev>_band_history.py`:

```sql
SET LOCAL search_path TO public, ag_catalog;

ALTER TABLE turns
    ADD COLUMN cefr_band TEXT
        CHECK (cefr_band IS NULL OR cefr_band IN ('A1','A2','B1','B2','C1'));

ALTER TABLE learner_profile
    ADD COLUMN stable_sessions_at_band INT NOT NULL DEFAULT 0,
    ADD COLUMN last_band_change_at     TIMESTAMPTZ;

CREATE TABLE band_history (
    id            BIGSERIAL PRIMARY KEY,
    from_band     TEXT CHECK (from_band IN ('A1','A2','B1','B2','C1')),
    to_band       TEXT NOT NULL CHECK (to_band IN ('A1','A2','B1','B2','C1')),
    reason        TEXT NOT NULL CHECK (
        reason IN ('placement','auto_promote','auto_demote','manual')
    ),
    signals       JSONB NOT NULL DEFAULT '{}'::jsonb,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX band_history_changed_at_idx ON band_history(changed_at DESC);
```

`from_band` nullable so the bootstrap row can record `from_band IS NULL`.
No row inserted at upgrade time; `band_history` is empty until session 1's
placement runs. `signals` JSONB captures the snapshot fields that drove
the decision (e.g. `{"mean_band_score": 0.46, "modal_band": "A2",
"valid_turns": 4, "decision_rule": "placement_mode"}`).

#### 3. Ingest + placement + leveling

`TurnIngestService._insert_turn` (today: `hable_ya/learner/ingest.py:96`)
threads `obs.cefr_band` into the `INSERT INTO turns` columns.

New module `hable_ya/learner/leveling/policy.py` contains pure functions:

```python
@dataclass(frozen=True, slots=True)
class PlacementDecision:
    band: CEFRBand
    signals: dict[str, Any]

@dataclass(frozen=True, slots=True)
class LevelingDecision:
    new_band: CEFRBand
    reason: Literal["auto_promote", "auto_demote", "stable"]
    signals: dict[str, Any]

BAND_TO_FLOAT: dict[CEFRBand, float] = {
    "A1": 0.1, "A2": 0.3, "B1": 0.5, "B2": 0.7, "C1": 0.9
}
BAND_BUCKETS: list[tuple[float, CEFRBand]] = [
    (0.20, "A1"), (0.40, "A2"), (0.60, "B1"), (0.80, "B2"), (1.01, "C1")
]

def place_band(
    turn_bands: Sequence[CEFRBand | None],
    *,
    floor_band: CEFRBand = "A2",
    min_valid_turns: int = 3,
) -> PlacementDecision | None:
    """Modal band over valid turns. Returns None if too few valid turns."""
    valid = [b for b in turn_bands if b is not None]
    if len(valid) < min_valid_turns:
        return None
    counts = Counter(valid)
    # Tie-break: when two bands tie, take the higher (gives the learner
    # the benefit; auto-leveling will demote later if wrong).
    modal = max(counts.items(), key=lambda kv: (kv[1], BAND_TO_FLOAT[kv[0]]))[0]
    band = max(modal, floor_band, key=lambda b: BAND_TO_FLOAT[b])
    return PlacementDecision(
        band=band,
        signals={
            "modal_band": modal,
            "counts": dict(counts),
            "valid_turns": len(valid),
            "total_turns": len(turn_bands),
            "floor_applied": band != modal,
        },
    )

def evaluate_leveling(
    *,
    current_band: CEFRBand,
    recent_turn_bands: Sequence[CEFRBand],   # already filtered non-None
    stable_sessions: int,
    promote_consecutive: int = 3,
    demote_consecutive: int = 4,
) -> LevelingDecision:
    """Pure: identical inputs → identical outputs."""
    if not recent_turn_bands:
        return LevelingDecision(current_band, "stable", {"reason": "no_data"})
    mean_score = mean(BAND_TO_FLOAT[b] for b in recent_turn_bands)
    target = bucket_band(mean_score)   # uses BAND_BUCKETS
    if target == current_band:
        return LevelingDecision(current_band, "stable", {...})
    if BAND_TO_FLOAT[target] > BAND_TO_FLOAT[current_band]:
        # Promotion path
        if stable_sessions + 1 >= promote_consecutive:
            return LevelingDecision(target, "auto_promote", {...})
        return LevelingDecision(current_band, "stable", {"toward": target, ...})
    # Demotion path
    lower_center = BAND_TO_FLOAT[target]   # bucket center, not boundary
    if mean_score > lower_center:
        # Borderline — don't start the demotion clock.
        return LevelingDecision(current_band, "stable", {"toward": "borderline", ...})
    if stable_sessions + 1 >= demote_consecutive:
        return LevelingDecision(target, "auto_demote", {...})
    return LevelingDecision(current_band, "stable", {"toward": target, ...})
```

`stable_sessions` semantics carry forward from the previous draft:
incremented when `target == current_band`, reset on a flip, accumulated
toward the threshold across consecutive same-target sessions.

New `hable_ya/learner/leveling/service.py` wraps the pure functions in
asyncpg I/O. Public surface: `LevelingService.run_placement(session_id)`
and `LevelingService.run_leveling()`. Same write transaction shape as
spec 029's `TurnIngestService`: read state, call pure decision, write
both `learner_profile` and `band_history` in one `conn.transaction()`.

`is_calibrated` becomes a single SELECT:

```python
async def is_calibrated(pool) -> bool:
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM band_history WHERE reason='placement')"
        ))
```

`snapshot_to_profile` (`hable_ya/learner/profile.py:45`) gains an explicit
`is_calibrated: bool` parameter; callers thread it through. Default
`False` for the eval-accumulator path.

#### 4. Pipeline wiring

`TurnIngestService.end_session` (`hable_ya/learner/ingest.py:88`) gains:

```python
async def end_session(self, *, session_id: str) -> None:
    async with self._pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET ended_at = now() WHERE session_id = $1",
            session_id,
        )
    if self._leveling is None:
        return
    try:
        if not await is_calibrated(self._pool):
            await self._leveling.run_placement(session_id=session_id)
        else:
            current_band = await self._profile.current_band()
            await self._leveling.run_leveling(current_band=current_band)
    except Exception:
        logger.exception("session %s: leveling failed", session_id)
        # Sink counter incremented elsewhere via the service.
```

`build_session_prompt` (`hable_ya/pipeline/prompts/builder.py:49`)
updates the cold-start gate:

```python
opt_in_cold_start = opt_in_cold_start or not await is_calibrated(pool)
```

Replaces the current `first_session = snapshot.sessions_completed == 0`
gate.

`api/main.py` constructs `LevelingService` in lifespan and attaches it
to `app.state.leveling`. `TurnIngestService` constructor gains
`leveling: LevelingService | None = None`.

#### 5. `COLD_START_INSTRUCTIONS` rewrite

`hable_ya/pipeline/prompts/register.py::COLD_START_INSTRUCTIONS` becomes:

```
Esta es la primera conversación con el estudiante. Tu objetivo es
estimar su nivel de español. Sigue esta progresión natural a lo largo
de la conversación:

1. Empieza saludando y pregunta por su nombre, de dónde es y qué hace
   (presentación básica).
2. Después, pregúntale sobre su rutina diaria o un tema cotidiano de
   su vida (presente, vocabulario familiar).
3. Después, pregúntale por algo que hizo recientemente — un viaje, un
   evento, un fin de semana (pasado, narración corta).
4. Finalmente, pregúntale su opinión sobre algo concreto — una
   película, una decisión, un cambio en su ciudad (presente abstracto,
   justificación).

Mantén la conversación natural, no anuncies que es un diagnóstico, no
preguntes por su nivel, y respeta el ritmo del estudiante. Si no
responde en español, hazle una pregunta más simple en español.

En cada `log_turn`, incluye `cefr_band` con tu evaluación del nivel del
estudiante en esa última intervención (consulta el rubro arriba).
```

Plus the new `BAND_RUBRIC` block in the regular `render_system_prompt`
output, applied to every session.

#### 6. `replay_placement.py`

```
python -m scripts.replay_placement [--bands A1,A2,B1,B2,C1] \
    [--limit N] [--verbose] [--llama-cpp-url URL]
```

For each fixture in `eval/fixtures/cold_start.json`:
1. Render the system prompt + COLD_START_INSTRUCTIONS via the same
   `render_system_prompt` the runtime uses.
2. Build a chat-completion request with `tools=HABLE_YA_TOOLS`.
3. Replay the four learner utterances as `user` messages. After each,
   capture the model's response + parse the `log_turn` tool call's
   `cefr_band`.
4. Apply `place_band(turn_bands, floor_band="A2")`.
5. Compare `decision.band` to fixture `true_band`; per-turn emitted
   bands to fixture `band_indicators`.

Reports:
- Per-fixture rows: fixture id, true band, predicted band, per-turn
  emitted bands, per-turn `band_indicators`, agreement.
- Per-band aggregates: accuracy + MAE per `true_band`.
- Overall: accuracy + MAE + 5×5 confusion matrix.
- `band_missing` count: how often the model omitted the field
  (failure-mode visibility).

Exits non-zero if `accuracy < 0.75` or `MAE > 0.20`. CI runs the script
on every PR. **The CI gate is `continue-on-error: true` in this slice's
landing PR**; whether it becomes blocking depends on the pre-merge
replay run (see Confidence + Open Question 3).

#### 7. Settings additions

```python
# hable_ya/config.py
leveling_window_sessions: int = 3       # session lookback for leveling
leveling_promote_consecutive: int = 3   # K for promotion hysteresis
leveling_demote_consecutive: int = 4    # K for demotion hysteresis
placement_min_valid_turns: int = 3      # below this, placement abstains
```

`default_learner_band` keeps its name; documented role is "bootstrap +
placement floor".

#### 8. Module ownership

- `hable_ya/tools/schema.py` — `LOG_TURN_TOOL` gains `cefr_band`.
- `hable_ya/pipeline/prompts/render.py` — `BAND_RUBRIC` constant +
  rendered section.
- `hable_ya/pipeline/prompts/register.py` — `COLD_START_INSTRUCTIONS`
  rewritten.
- `hable_ya/runtime/observations.py` — `TurnObservation.cefr_band`,
  sink `band_missing` counter.
- `hable_ya/pipeline/processors/tool_handler.py` — parse + validate
  `cefr_band` from the tool call; degrade to `None` on missing/invalid.
- `hable_ya/learner/ingest.py` — thread `cefr_band` into
  `INSERT INTO turns`; call `LevelingService` from `end_session`.
- `hable_ya/learner/leveling/__init__.py` — re-exports.
- `hable_ya/learner/leveling/policy.py` — pure decisions.
- `hable_ya/learner/leveling/service.py` — async wrapper.
- `hable_ya/learner/profile.py` — `snapshot_to_profile` gains
  `is_calibrated` parameter; new `is_calibrated_async(pool)` helper;
  `current_band(pool)` helper.
- `hable_ya/pipeline/prompts/builder.py` — cold-start gate uses
  `is_calibrated`.
- `api/main.py` — constructs and attaches `LevelingService`.
- `api/routes/dev.py` — extended `/dev/learner`.
- `scripts/replay_placement.py` — new.
- `eval/agent/types.py` — `TurnRecord.cefr_band: CEFRBand | None`.
- `eval/agent/accumulator.py` — accept + carry `cefr_band` through
  (passthrough; no aggregation logic added).
- `hable_ya/db/alembic/versions/<rev>_band_history.py` — schema.
- New `tests/test_placement_policy.py`,
  `tests/test_leveling_policy.py`, `tests/test_band_history.py`.
- Updated `tests/test_prompts.py` (cold-start + BAND_RUBRIC content),
  `tests/test_dev_endpoints.py`,
  `tests/test_log_turn_ingestion.py`,
  `tests/test_tool_handler.py` (cefr_band parsing happy + degraded).

### Confidence

**Level:** Low

**Rationale:** The infrastructure pieces — alembic revision in
spec-028 style, asyncpg + transactions, prompt content rewrite, dev
endpoint extension, pure-function policies — are well-trodden after
specs 028 and 029. The repo's testing rig (`db_pool` fixture,
byte-identity prompt tests, CI lint scope) is ready to absorb the new
modules. The placement and leveling algorithms got significantly simpler
than the original heuristic-weighted-score draft (mode + rolling mean of
labels), which raises confidence on the policy layer.

What pulls confidence to Low:

1. **Prompt-engineering reliability is unproven.** The fine-tuned Gemma
   was trained on a 4-field `log_turn` schema. We're asking it to emit a
   5th field (`cefr_band`) it has never been trained on, purely via tool
   description + a `BAND_RUBRIC` system-prompt section. The model is
   instruction-following but: (a) emission rate may drop (the existing
   ~80% may degrade further when the schema is more demanding), (b) the
   bands emitted may not track ground truth — the model may have
   coherent-but-wrong calibration (e.g., always emits B1 regardless), (c)
   the model may emit out-of-enum strings ("intermediate", "B1+"). The
   `replay_placement.py` script is the only way to find out, and it
   requires the new prompt content to be authored before it can run.
2. **The CI regression bar may not be reachable without fine-tuning.**
   `accuracy ≥ 0.75` against the labeled cold_start fixtures is a real
   bar the eval harness has lived with for the recast/tool metrics where
   the model was explicitly trained. For `cefr_band` it's untrained.
   Ship-anyway behavior (Open Question 3) is the proposal: land the
   substrate, mark the CI gate advisory if the bar isn't met, open a
   follow-up fine-tune spec.
3. **Hysteresis windows are intuition.** K=3 / K=4 feel right but session-
   to-session variance of the model's emitted bands (vs the rolling mean
   of features) is unmeasured. May need K=5 or K=8 in practice; tunable
   via settings, but the default is a guess.
4. **`is_calibrated` semantics ripple.** Several places read it today:
   `snapshot_to_profile`, the cold_start fixture's
   `expected_profile_update.is_calibrated`, the eval harness's fixture
   comparator. The change from `sessions_completed > 0` to "placement row
   exists" must be threaded through; mechanical but easy to miss a site.

**Validate before proceeding:**

1. **Author the BAND_RUBRIC + run `replay_placement.py` against a
   stand-alone prototype** before writing service code. The prototype: a
   single-file script that builds the prompt, posts the four learner
   utterances of three fixtures (one per band cluster: A1, B1, C1) to
   llama.cpp, parses the response. If the model never emits a valid
   `cefr_band`, or emits one but never the right one, the prompt-only
   approach is dead and the spec converts to a fine-tune-first plan
   before any runtime code lands. Time-box: ~half a day.
2. **Tune the rubric.** Once the prototype emits *something*, iterate the
   rubric content (more concrete production examples per band, sharper
   contrasts at adjacent boundaries) against the full 25-fixture replay.
   Goal: clear `accuracy ≥ 0.75`. If iteration plateaus below the bar,
   record the plateau accuracy in the decision record and ship with
   advisory CI.
3. **One live conversation against the rewritten
   COLD_START_INSTRUCTIONS.** A 5-minute hands-on session. Observe
   whether the four-step ladder gets traversed organically. Whether the
   model emits valid bands per turn. Whether the diagnostic feels like a
   conversation or an interview.

### Key Decisions

1. **Bundle 049 + 050.** Same write path, same primary signal, same
   end-of-session lifecycle hook. Pattern: spec 029.
2. **Model-emitted band is the primary signal, not a heuristic.** Per
   user direction, and matches the original product framing (model as
   pedagogical assessor). The 4-field `log_turn` schema today is half
   the assessor's job; this slice completes it.
3. **Prompt engineering first; fine-tuning is a separate spec.** The
   fine-tuned Gemma is trained on 4-field `log_turn` (project memory:
   fine-tune scope is `recast_present` + `tool_args_correct` only).
   Adding the field is a prompt-only change in this slice. If the
   regression bar isn't met, a follow-up spec adds it to the SFT data.
4. **No heuristic fallback when the model omits the band.** Placement
   abstains; the user re-enters the diagnostic. This makes "the model
   isn't doing its job" loud instead of papering over it.
5. **Modal band for placement, mean band for leveling.** Mode is robust
   to one outlier turn (more in the right register of the placement
   problem); mean is appropriate for the trend signal that drives
   leveling. Both operate over the same `cefr_band` field; the
   difference is the temporal window.
6. **Asymmetric hysteresis (K=3 promote, K=4 demote) + demotion-only
   crossing margin.** A bad week shouldn't knock the learner down. A
   borderline session shouldn't start the demotion clock at all.
7. **Pure-function policies + thin async service wrapper.** Mirrors
   `compute_snapshot` / `LearnerProfileRepo.get` from spec 029.
   `replay_placement.py` and unit tests don't need a DB.
8. **`is_calibrated` derived from `band_history`, not session count.** A
   completed session that produced no `log_turn` calls (pathological but
   possible) must not flip `is_calibrated`. Keying on "did placement
   actually run" makes the semantics correct.
9. **Run leveling at end-of-session, not session-start.** Connect
   latency unaffected.
10. **Audit row per band change, indexed on `changed_at`.** Tiny table,
    surfaced in `/dev/learner`. Future #042 reads it.
11. **`default_learner_band` keeps name + becomes placement floor +
    bootstrap.** Naming inertia outweighs clarity win.
12. **CI gate is advisory in this slice's landing PR.** If
    `replay_placement.py` doesn't clear the bar with prompt-only, the
    substrate still lands; the gate is advisory until a follow-up
    fine-tune lifts the model's band emission to spec.

### Testing Approach

The repo's pytest rig (`db_pool` / `db_conn` session-scoped fixtures
from spec 028) absorbs the new tests. DB tests skip cleanly when
Postgres is unreachable.

**Unit tests (no DB):**

- `tests/test_placement_policy.py`:
    - `place_band` returns `None` when fewer than `min_valid_turns`
      non-None bands provided.
    - `place_band([A1, A1, A2, A1])` → `A1`.
    - `place_band([A2, A2, B1, B1])` → `B1` (tie-break to higher).
    - `place_band([C1, C1, C1, C1], floor_band="A2")` → `C1`.
    - `place_band([A1, A1, A1, A1], floor_band="A2")` → `A2` (floor
      kicks in).
    - Pure: same inputs → same outputs.

- `tests/test_leveling_policy.py`:
    - All-current-band recent turns → `stable`.
    - Higher-band rolling mean for K-1 sessions → `stable` (still
      accumulating); K-th call → `auto_promote`.
    - Lower-band rolling mean: borderline (mean above lower-bucket
      center) → never promotes the demotion clock.
    - Lower-band rolling mean: clearly below lower-bucket center,
      K-1 sessions → `stable`; K-th call → `auto_demote`.
    - Mixed sequence: 2 promote-target + 1 stable + 1 promote does
      NOT promote (target_persists check via stable_sessions reset).
    - Floor: `current_band="A1"`, target lower → no-op.
    - Ceiling: `current_band="C1"`, target higher → no-op.

- Updated `tests/test_prompts.py`:
    - Byte-identity tests for new `COLD_START_INSTRUCTIONS` content.
    - Byte-identity tests for `BAND_RUBRIC` section in the regular
      `render_system_prompt` output.
    - Test that `LOG_TURN_TOOL["function"]["parameters"]["properties"]`
      contains `cefr_band` with the expected enum.

- Updated `tests/test_tool_handler.py`:
    - Happy path: tool call with valid `cefr_band="B1"` → observation
      carries it.
    - Missing `cefr_band` field → observation has `cefr_band=None`,
      `band_missing` counter incremented, no exception.
    - Out-of-enum `cefr_band="intermediate"` → same degraded path.

**Integration tests (use `db_pool`):**

- `tests/test_band_history.py`:
    - Migration applies cleanly: `band_history` exists, `turns.cefr_band`
      column exists, `learner_profile.stable_sessions_at_band` column
      exists.
    - `LevelingService._apply_band_change` inserts the row, updates
      `learner_profile`, resets `stable_sessions_at_band`.
    - `is_calibrated` False on fresh DB; True after a placement row.
    - `/dev/learner` returns the recent 5 `band_history` rows in
      `changed_at DESC` order.

- Extended `tests/test_log_turn_ingestion.py`:
    - Feed 4 `TurnObservation` instances with `cefr_band="A1"`; call
      `end_session`; assert `learner_profile.band == 'A1'` (or floor),
      `band_history` has one `placement` row, `is_calibrated` True.
    - Feed a session of `cefr_band="B2"` turns after placement at A2 →
      `stable_sessions_at_band` increments toward 3; on the third such
      session, `learner_profile.band == 'B2'`.
    - Three sessions of `cefr_band=None` turns post-placement →
      leveling no-ops (no_data branch); `learner_profile.band`
      unchanged.
    - DB-write failure path (close pool mid-call) → `leveling_failed`
      counter increments, no exception escapes.

**Manual + integration validation (out of pytest):**

- **Prototype run** (Validate #1 above): single-file script against
  three fixtures (A1, B1, C1) before writing any service code. Confirms
  the model emits `cefr_band` at all and that emissions cluster near
  ground truth.
- **Full replay run**: `python -m scripts.replay_placement --verbose`
  on the 25 fixtures. Reviewed pre-merge. Result determines whether the
  CI gate ships blocking or advisory.
- **Live conversation**: 5-minute Spanish session against the rewritten
  `COLD_START_INSTRUCTIONS`. Disconnect. `psql`:
    - `SELECT cefr_band FROM turns WHERE session_id = ... ORDER BY
      timestamp` matches the conversation level.
    - `SELECT band, sessions_completed, stable_sessions_at_band FROM
      learner_profile`.
    - `SELECT * FROM band_history` has the placement row.
- **Reconnect** for session 2: prompt is no longer cold-start, theme
  rotation kicks in, `## Topic:` is a real scenario.
- **Three sessions above-band**: speak deliberately above the placed
  band. Confirm the band flips on session 4 (placement + 3 promote),
  not earlier or later. `band_history` has an `auto_promote` row.

This slice closes the loop the 029 substrate was built for: the band
becomes a real, model-derived, audited signal. The model finally does
the assessment job that fluency_signal + L1_used + errors only
half-completed. Whether the prompt alone gets us there — or whether a
fine-tune is needed — is the open empirical question this spec exists
to answer.
