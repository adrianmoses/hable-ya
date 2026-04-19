# Spec: Agent Loop — Band-Adapted Prompt, Tool Schema, `log_turn` Handler

| Field | Value |
|---|---|
| id | 023 |
| status | approved |
| created | 2026-04-19 |
| covers roadmap | #023, #024, #025, #027 (bundle) |

---

## Why

Spec 021 wired the Pipecat voice loop and spec 046 put a browser in front of it, but the agent at the end of the wire is a placeholder. `hable_ya/pipeline/prompts/builder.py` emits the same band-agnostic prompt for every learner; `hable_ya/tools/schema.py::HABLE_YA_TOOLS` is an empty list; `hable_ya/pipeline/processors/tool_handler.py` only *strips* `log_turn(...)` from TTS and throws the payload away; `hable_ya/pipeline/prompts/register.py` is a stub with empty strings. The authoritative per-band prompt (register guidance, FORBIDDEN_CORRECTION_PHRASES, worked recast examples, canonical tool-call shape) already exists in `finetune/format.py::_render_system_prompt()` and is the single source of truth for both eval and fine-tune training — but the runtime does not use it. As a result, the learner can talk to the pipeline, but they don't yet get band-appropriate responses, recast behavior is unconstrained, and the `log_turn` signal the whole learner-model story depends on is emitted by the model and then discarded.

This spec fills in the agent loop end to end — minus persistence, which is deliberately deferred to its own slice (#026 + #028).

### Consumer Impact

- **End user (learner):** Receives band-appropriate Spanish replies with explicit recast behavior and no explicit-correction phrases. Feels the model adapt its register to their level rather than produce generic intermediate-register output. No surface changes from #046 — same screen, same orb, same pipeline; what changes is what comes out of the speaker.
- **Project owner (researcher/developer):** Gets structured `log_turn` observations out of every real session (parsed, schema-validated, written to `runtime_turns.jsonl` for inspection). This is the first time the fine-tune targets (`recast_present`, `tool_args_correct`) can be evaluated on live conversation data rather than fixture playback — which directly feeds what to tune next. The currently-deployed fine-tuned Gemma emits `log_turn` on ~80% of turns per fixture evals; the `missing_log_turn_calls` counter on the live sink is the way to confirm that number holds against real sessions.
- **Downstream features:** #026 (turn persistence) replaces the JSONL sink with a durable store. #029–#033 (learner model) consumes the observation stream. #034–#036 (agent eval) can run synthetic-learner sessions against the real runtime once the loop is coherent.

### Roadmap Fit

Bundles four planned items:

- **#023** System prompt builder unified with `finetune/format.py` content.
- **#024** `REGISTER_BY_LEVEL` + `COLD_START_INSTRUCTIONS` content.
- **#025** Tool handler consuming `log_turn` calls from LLM output.
- **#027** `HABLE_YA_TOOLS` schema definition.

They're bundled because splitting them would produce four PRs that individually land non-functional stubs (prompt without schema, schema without handler, handler without observer). Persistence (#026) is explicitly out — it requires the DB stack (#028) and schema design that should happen only once the shape of a "turn observation" is known in practice.

Dependencies:

- Upstream: #021 (voice pipeline) ✓, #014 (SFT dataset generator) and `finetune/format.py` ✓ (source of truth for prompt content).
- Downstream (unblocked by this): #026 (persistence of observations), #029–#033 (learner model consumers), #034–#036 (agent eval).

---

## What

### Acceptance Criteria

With llama.cpp + `api/main.py` + `web/` running:

- [ ] The system prompt the runtime sends to llama.cpp for a session configured as band `A2` is **byte-identical** to what `finetune.format._render_system_prompt(params, band="A2")` produces from a fixture with matching `SystemParams` — modulo the theme block, which may be populated with a neutral default in the runtime. Equivalent identity checks pass for A1, B1, B2, C1.
- [ ] `hable_ya.pipeline.prompts.register.REGISTER_BY_LEVEL` is populated for all five bands (A1–C1) with the same strings used in `finetune/format.py::_REGISTER_GUIDANCE`. The two constants are sourced from a single module (no duplication).
- [ ] `hable_ya.pipeline.prompts.register.COLD_START_INSTRUCTIONS` is a non-empty string of session-opening guidance used on session 1 (no diagnostic flow — see Non-Goals).
- [ ] `hable_ya.tools.schema.HABLE_YA_TOOLS` is a non-empty list containing at least the `log_turn` tool with its full JSON-schema argument definition. Consumers that would use it (LLM-service `tools=` parameter) can import and pass it without transformation.
- [ ] During a live session, when the model emits `log_turn({...})` (function-call text form) or `[TOOL_CALL: log_turn]{...}` (legacy form) in its response, the tool handler:
    1. Parses the call, using the shared parser at `eval/scoring/turn.py::parse_tool_calls`.
    2. Validates the arguments against a canonical schema (keys: `learner_utterance`, `errors`, `fluency_signal`, `L1_used`). Missing optional keys get empty defaults; unknown keys are preserved; a fundamentally malformed call is logged as a warning and dropped (graceful degradation — see Key Decisions).
    3. Writes the validated observation as one JSON line to a configurable path (default `runtime_turns.jsonl` under the project's runtime data dir), with a timestamp and session id.
    4. Strips the tool-call text from the response so it never reaches TTS (existing behavior, preserved).
- [ ] The last N (default 100) observations are available in memory on the running `VoiceService` / app state for debugging via an unauthenticated dev-only endpoint or a REPL hook. (Open Question #1 below — may collapse to "logs only" depending on answer.)
- [ ] `settings.default_learner_band: str = "A2"` (env: `DEFAULT_LEARNER_BAND`) is the knob that selects the band for all sessions until the learner model lands. `DEFAULT_LEARNER` in `runner.py` reads from it.
- [ ] When the model's response contains **no** valid `log_turn` call (the fine-tuned Gemma currently emits the call ~80% of the time; the remaining ~20% must not crash the pipeline), the pipeline proceeds normally; a `missing_log_turn_calls` counter increments and a warning is logged. The session continues; the orb continues to animate; the learner is unaffected.
- [ ] The existing tool-strip behavior (current `HableYaToolHandler`) still produces TTS-clean text identical to today — no regression.
- [ ] `pytest` passes, including the new tests called out in the Testing Approach section.

### Non-Goals

- **No persistence** (#026). The JSONL sink is a dev artifact. No Postgres, no AGE, no schema migrations, no writes to a learner profile.
- **No cold-start diagnostic flow.** `COLD_START_INSTRUCTIONS` is static text; the agent doesn't ask diagnostic questions, doesn't infer band from learner responses, doesn't adjust register mid-session.
- **No dynamic learner profile.** The L1-reliance, fluency, error-patterns, and vocab-strengths fields in the prompt come from static defaults (initially band-adjusted neutrals). No read from any datastore, no update from `log_turn` back into the prompt within a session.
- **No theme selection** (#032). The prompt uses a fixed neutral theme ("una conversación abierta — el estudiante elige de qué hablar"). `THEMES_BY_LEVEL` stays empty; `get_session_theme` stays `NotImplementedError`.
- **No side-channel events to the frontend.** `log_turn` observations don't cross the WS back to the browser. The web UI is unchanged. A future spec (captions / learner state / recap) owns that protocol.
- **No tool other than `log_turn`.** `HABLE_YA_TOOLS` defines only `log_turn`. `log_error` and other auxiliaries mentioned in `finetune/format.py` comments are out — they're dropped intentionally per the fine-tune scope.
- **No model-behavior guarantees.** Untuned Gemma may still produce mediocre Spanish, skip `log_turn`, or ignore the FORBIDDEN phrases. This spec wires the *runtime*; the model's actual quality is a separate concern and the fine-tune workstream (#016) is already in flight.
- **No agent-eval automation** (#034–#036). Those test synthetic-learner sessions against this loop; they're separate specs.

### Open Questions

1. ~~**Dev-only endpoint for observation ring buffer?**~~ **Resolved: yes.** `GET /dev/observations` returns the ring buffer as JSON, gated by `settings.dev_endpoints_enabled: bool = False`. A `WARNING` is logged at startup when enabled. Not wired into the frontend.

2. ~~**Where does the canonical prompt renderer live?**~~ **Resolved: Option A.** Move `_render_system_prompt` and its dependencies (`_REGISTER_GUIDANCE`, `_RECAST_EXAMPLES`, `FORBIDDEN_CORRECTION_PHRASES`, `_band_from_production_level`) out of `finetune/format.py` into `hable_ya/pipeline/prompts/render.py`. Both `finetune/format.py` and `hable_ya/pipeline/prompts/builder.py` import from there.

3. **Ring-buffer size and JSONL rotation policy.** Ring buffer default 100 observations (roughly 1–2 sessions). JSONL appends forever in dev — user clears manually. Small (~1 KB per observation), throwaway when #026 lands. Documented; no further resolution needed.

---

## How

### Approach

**Module moves and new files.**

- New: `hable_ya/pipeline/prompts/render.py` — the canonical prompt renderer (`render_system_prompt`), register guidance, recast examples, forbidden-phrase list. Moved verbatim (plus minor renames to drop leading underscores on exports) from `finetune/format.py`. Pure, stateless, no I/O.
- Updated: `finetune/format.py` imports `render_system_prompt`, `FORBIDDEN_CORRECTION_PHRASES`, `REGISTER_GUIDANCE`, `RECAST_EXAMPLES` from the new module. Existing `_render_system_prompt` is removed; the wrapper used by `fixture_to_sft` calls the public function. Existing eval + fine-tune tests should continue to pass unchanged.
- Updated: `hable_ya/pipeline/prompts/register.py` now re-exports `REGISTER_GUIDANCE as REGISTER_BY_LEVEL` and defines a non-empty `COLD_START_INSTRUCTIONS` string. No duplication.
- Updated: `hable_ya/pipeline/prompts/builder.py::build_system_prompt(learner)` builds a `SystemParams` from the `learner` dict + `settings.default_learner_band`, calls `render_system_prompt`, returns the string. Replaces the placeholder.

**Tool schema.**

- Updated: `hable_ya/tools/schema.py::HABLE_YA_TOOLS` becomes a list with one JSON-schema entry for `log_turn`, matching the canonical keys from `finetune.format`: `learner_utterance` (string), `errors` (list of `{type, produced, target}`), `fluency_signal` (enum `weak`/`moderate`/`strong`), `L1_used` (bool). All keys required.
- `hable_ya/pipeline/services.py` passes `HABLE_YA_TOOLS` into the LLM service's `tools=` parameter. For llama.cpp's OpenAI-compat endpoint with a Gemma model trained on plain-text function calls, this is best-effort — the model has been reinforced to emit the text form regardless, and the eval parser already handles both.

**Tool handler.**

- Rewritten: `hable_ya/pipeline/processors/tool_handler.py`. Keep the buffer-until-`LLMFullResponseEndFrame` pattern. New responsibilities:
    1. Run `parse_tool_calls(buffered_text, api_tool_calls=None)` (reusing `eval.scoring.turn.parse_tool_calls`).
    2. For each parsed call with `name == "log_turn"`, normalise args (reuse `finetune.format._normalize_log_turn_args` — or its re-exported public form) into the canonical 4-key shape. Validate types. Drop (warn) if fundamentally malformed (e.g. `errors` not a list, `L1_used` not coercible to bool).
    3. Emit the validated observation through a new `TurnObservationSink` abstraction (next bullet).
    4. Run the existing `strip_tool_calls` on the buffered text and push the cleaned `LLMTextFrame`.
    5. If zero `log_turn` calls were parsed, increment a counter on the sink (`sink.missing += 1`) and log a warning.

**Observation sink.**

- New: `hable_ya/runtime/observations.py` — module defining `TurnObservation` (dataclass: `session_id`, `timestamp_iso`, `learner_utterance`, `errors`, `fluency_signal`, `L1_used`, plus raw passthrough for unknown keys) and a small `TurnObservationSink` class with:
    - `append(obs: TurnObservation) -> None` — writes one JSONL line to `settings.runtime_turns_path` (default `runtime_turns.jsonl` under a dev data dir), keeps the last N (default 100) in a `collections.deque`.
    - `recent(n: int) -> list[TurnObservation]` — ring-buffer read.
    - `missing: int` counter.
- The sink is constructed once at app startup (in the FastAPI lifespan) and attached to `app.state.observation_sink`. `HableYaToolHandler` takes the sink as a constructor arg.
- JSONL append uses unbuffered writes under a lock so the file is safe even if multiple `/ws/session` connections open (pipeline guarantees single active session per deployment anyway — single-tenant — but correctness here costs nothing).

**Dev endpoint (Open Question #1 pending).** If approved: `GET /dev/observations` returns `sink.recent()` as JSON. Gated by `settings.dev_endpoints_enabled`. Emits a `WARNING` log line on startup when enabled. Not wired into the frontend.

**Settings.**

- New: `settings.default_learner_band: str = "A2"` (env `DEFAULT_LEARNER_BAND`).
- New: `settings.runtime_turns_path: Path` (default `Path("runtime_turns.jsonl")` resolved against CWD).
- New: `settings.observation_ring_size: int = 100`.
- New: `settings.dev_endpoints_enabled: bool = False`.

**Graceful degradation.** The pipeline must not break if the model skips `log_turn`, emits malformed args, or emits multiple `log_turn` calls in one response. All three cases log a warning and proceed. The sink's `missing` counter is the surface for the project owner to confirm the currently-deployed fine-tuned model's ~80% emission rate on live sessions (vs. the fixture-eval measurement) and to track drift over time.

### Confidence

**Level:** High

**Rationale:** The heavy lifting — prompt content, tool parsing, tool-call stripping, schema — already exists in `finetune/format.py` and `eval/scoring/turn.py` and has been exercised against hundreds of fixtures. This spec is mostly connecting wires already laid. The one uncertainty I initially flagged — whether the currently deployed model would emit `log_turn` at runtime at all — is retired: the fine-tuned Gemma in use today is known to emit the call on ~80% of turns. That number is itself a valuable baseline the `missing_log_turn_calls` counter will now confirm against live sessions.

Remaining minor unknowns, none of which block implementation:

- Does the model stream `log_turn(...)` before the Spanish reply, after it, or interleaved? The handler buffers the whole response before stripping and parsing, so any ordering works.
- Does tool-call JSON survive token streaming cleanly? The handler waits for `LLMFullResponseEndFrame`, so parsing happens on the complete payload — no risk of mid-stream truncation.
- Will the model obey the FORBIDDEN phrase list? A model-quality question outside this spec. If real-session data shows drift, a `forbidden_phrase_detected` counter is a trivial add in a follow-up.

**Validate before proceeding:**

1. **Open Question #1** (dev endpoint for observation ring buffer) — 1-line decision, resolve with the user.
2. **Open Question #2** (prompt renderer location) — I've recommended Option A; confirm.

### Key Decisions

1. **Bundle 023/024/025/027; defer 026.** Splitting the prompt + schema + handler would produce non-functional intermediates. Persistence is a separate concern with its own dependencies (#028 Postgres+AGE). The JSONL sink is throwaway when #026 lands.
2. **Single source of truth for prompt rendering** in `hable_ya/pipeline/prompts/render.py`, imported by both the runtime and `finetune/format.py`. Inverts the current implicit dependency cleanly.
3. **Graceful degradation over strict validation.** Untuned Gemma will skip or mangle `log_turn` calls; the runtime must not crash. Log + counter + drop.
4. **JSONL sink, not DB.** One-line appends, human-readable, no migration cost when #026 replaces it.
5. **No side-channel to frontend in this slice.** Keeps the frontend stable, keeps the events-protocol question (flagged by spec 046's decision record) unresolved until its own spec.
6. **Band via settings, not per-session.** Every session uses `settings.default_learner_band`. When the learner model lands, the band will come from the learner profile, not the config; the `DEFAULT_LEARNER` dict's band field is the future consumption point.

### Testing Approach

The repo's pytest suite (`OVERVIEW.md §Testing Suite`) already covers scoring heuristics and fixture validation. New tests fill in two stubs (`test_prompts.py`, `test_tools.py`) and add a new module test. Agent-eval automation (#034–#036) is out of scope.

**Unit tests to write:**

- `tests/test_prompts.py`:
    - `build_system_prompt({"band": "A2", ...})` is byte-identical to `finetune.format.render_system_prompt(SystemParams(profile=<default>, theme=<neutral>), band="A2")`.
    - Repeat for A1, B1, B2, C1.
    - Output contains each `FORBIDDEN_CORRECTION_PHRASE`.
    - Output contains the canonical `log_turn(...)` example line.
- `tests/test_tools.py`:
    - `HABLE_YA_TOOLS` is non-empty; contains exactly one tool named `log_turn`.
    - The `log_turn` schema validates a well-formed call with `jsonschema` (or a minimal manual check).
    - The schema rejects missing `learner_utterance`, non-list `errors`, invalid `fluency_signal`, non-bool `L1_used`.
- `tests/test_tool_handler.py` (new file):
    - Given a fabricated frame stream with `LLMFullResponseStartFrame → LLMTextFrame("Hola. ") → LLMTextFrame('log_turn({"learner_utterance":"hola","errors":[],"fluency_signal":"moderate","L1_used":false})') → LLMFullResponseEndFrame`, the handler (a) calls `sink.append` once with a parsed `TurnObservation` whose fields match, (b) emits a single cleaned `LLMTextFrame("Hola.")`, (c) does not increment `sink.missing`.
    - Given a stream with no tool call, the handler emits the buffered text unchanged and `sink.missing` increments by 1.
    - Given a stream with malformed args (`"errors": "not-a-list"`), the observation is dropped, `sink.missing` increments, cleaned text is still emitted.
- `tests/test_observations.py` (new file):
    - `TurnObservationSink.append` writes one valid JSON line to a tmp path.
    - `sink.recent(n)` returns the last `n` appended observations.
    - Ring buffer caps at `observation_ring_size`.

**Manual validation (out of pytest, human-run):**

- With `api/main.py` + llama.cpp warm, open `web/` and hold a ~2-minute Spanish conversation. Confirm: (a) `runtime_turns.jsonl` contains one line per agent turn that included a `log_turn` call, (b) the lines are well-formed JSON with the canonical keys, (c) TTS never speaks the tool-call text, (d) the counter `sink.missing` roughly matches visual observation of turns where the orb went agent → idle without a corresponding JSONL line.
- Repeat with `DEFAULT_LEARNER_BAND=A1` and `=C1`; confirm the agent's Spanish adapts register visibly (shorter sentences and simpler vocab at A1; longer and richer at C1).

This is the first slice where the runtime produces its own pedagogical signal; the JSONL file itself is the artifact the project owner will actually use to evaluate whether baseline vs. fine-tuned Gemma is producing different-quality `log_turn` calls on live sessions.
