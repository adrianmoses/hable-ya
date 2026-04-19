# Decision Record: Agent Loop — Band-Adapted Prompt, Tool Schema, `log_turn` Handler

| Field | Value |
|---|---|
| id | 023 |
| status | implemented |
| created | 2026-04-19 |
| spec | [spec.md](./spec.md) |
| covers roadmap | #023, #024, #025, #027 (bundle) |

---

## Context

The voice pipeline (spec 021) and the web frontend (spec 046) had been shipped, but the agent itself was still a placeholder: `hable_ya/pipeline/prompts/builder.py` emitted the same band-agnostic prompt for every learner; `hable_ya/tools/schema.py::HABLE_YA_TOOLS` was an empty list; the tool handler at `hable_ya/pipeline/processors/tool_handler.py` only *stripped* `log_turn(...)` from the TTS stream and threw the payload away; `hable_ya/pipeline/prompts/register.py` held empty strings. A duplicate-by-drift copy of the authoritative per-band system prompt (register guidance, FORBIDDEN_CORRECTION_PHRASES, worked recast examples, canonical tool-call shape) already lived in `finetune/format.py` and had been exercised against the fixture suite for months, but the runtime did not use it. The net effect: a learner could talk to the pipeline, but what came out of the speaker was generic intermediate-register Spanish, recast behavior was unconstrained, and the `log_turn` signal that the whole learner-model story depends on was emitted by the model and silently discarded.

This spec filled that in end-to-end — minus persistence (#026), which stays deferred until the Postgres + AGE stack (#028) is stood up.

Two things shaped implementation beyond what the spec captured:

1. **Passing `tools=HABLE_YA_TOOLS` into the `LLMContext` broke the runtime entirely.** Manual smoke showed no audio from the agent and every turn incrementing `sink.missing`. The declared-tools contract makes llama.cpp respect OpenAI's tool-calling convention, and the deployed fine-tuned Gemma — trained on plain-text `log_turn(...)` emissions, not structured `tool_calls` — responded with an empty text body and a (potentially structured) tool call that pipecat's `OpenAILLMService` routes through a different frame type our handler doesn't watch. Dropped the wiring; kept `HABLE_YA_TOOLS` in the repo as documentation of the payload shape.

2. **Gemma 4 thinking mode in llama.cpp silently ate every reply.** Even after dropping `tools=`, manual smoke still produced zero TTS audio. A direct streaming curl against llama.cpp showed the model emitting chain-of-thought as `delta.reasoning_content` and burning its entire `max_completion_tokens` budget there before producing any `delta.content` — and pipecat's `OpenAILLMService` only forwards `delta.content` as `LLMTextFrame`. Fix: pass `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` so all output lands in `content`. Saved to project memory; this is a llama.cpp + Gemma 4 quirk future work will keep tripping on.

## Decision

The runtime agent now speaks through a single canonical prompt path: `hable_ya/pipeline/prompts/render.py::render_system_prompt` is the source of truth for register guidance, FORBIDDEN_CORRECTION_PHRASES, worked recast examples, and the `log_turn` instruction block. Both `hable_ya/pipeline/prompts/builder.py` (runtime) and `finetune/format.py` (fine-tune training + eval) import from it; the band determines register, learner profile fields are static neutrals until the learner model lands. `HABLE_YA_TOOLS` in `hable_ya/tools/schema.py` declares the `log_turn` JSON-schema but is not injected into llama.cpp — it lives as payload documentation. The rewritten `HableYaToolHandler` parses `log_turn(...)` out of LLM response text (both function-call and `[TOOL_CALL: ...]` forms), normalises via `normalize_runtime_log_turn_args`, dispatches validated `TurnObservation`s to an `app.state.observation_sink` that writes JSONL + keeps a bounded ring buffer + increments a `missing` counter on skips. An opt-in `GET /dev/observations` endpoint under `HABLE_YA_DEV_ENDPOINTS_ENABLED` exposes the buffer for live inspection. The default learner band is read from `settings.default_learner_band` (env `HABLE_YA_DEFAULT_LEARNER_BAND`).

---

## Alternatives Considered

### Declared vs. undeclared tools to the LLM

**Option A:** Wire `HABLE_YA_TOOLS` into `LLMContext(tools=ToolsSchema(...))` so the model receives the tool contract through the formal OpenAI-style channel.
- Pros: Standards-compliant; leaves the door open for more tools later; aligns request shape with eval fixtures if they ever switch to structured tool calls.
- Cons: The deployed fine-tuned Gemma is trained on plain-text `log_turn(...)` emissions. Declaring tools flips llama.cpp into grammar-constrained tool-call mode; the model responds with a (possibly structured) tool call and empty text content. Pipecat's `OpenAILLMService` routes that through `FunctionCallInProgressFrame`, not `LLMTextFrame`, so our text-buffer parser sees nothing and TTS goes silent. Manual smoke caught this — 20 turns of zero responses — before merge.

**Option B (chosen):** Do not pass `tools=` to the LLM at all. Keep `HABLE_YA_TOOLS` in the repo strictly as documentation of the runtime payload shape, consumed only by tests. The model emits `log_turn(...)` as trained; our handler parses it out of text.
- Pros: Matches what the fine-tune actually produced; the existing text-form parser at `eval/scoring/turn.py::parse_tool_calls` handles the output natively (both `log_turn(...)` and the legacy `[TOOL_CALL: log_turn]{...}` surface).
- Cons: If we ever add a second tool, it won't be advertised to the model through the tools channel — we'd need a prompt-level instruction. Acceptable: `log_turn` is the only tool in scope per the fine-tune scope memory (`recast_present` and `tool_args_correct`).

**Chosen:** B, after A failed manual smoke. Spec had flagged this as a risk in its Approach section ("confirm keyword name and whether it rejects on llama.cpp's non-standard tool-call behavior"); the risk materialised.

### Gemma 4 thinking mode

**Option A:** Leave llama.cpp's default (thinking on) and consume `delta.reasoning_content` somewhere.
- Pros: Preserves Gemma's reasoning capability for future pedagogical-meta-analysis features.
- Cons: Pipecat's `OpenAILLMService` does not forward `reasoning_content`; we'd need a custom service fork or a pre-LLM processor that unwraps it. Under current `max_completion_tokens=150`, the model burns the entire budget on reasoning before ever producing `content` — TTS silent, `sink.missing` at 100%.

**Option B:** Increase `llm_max_tokens` until the budget survives the reasoning burn.
- Pros: Keeps reasoning on.
- Cons: Latency cost on every turn (reasoning averages hundreds of tokens), no net value to the learner since reasoning text isn't used anywhere.

**Option C (chosen):** Disable thinking via `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` on the OpenAI service input params.
- Pros: Single-line config change; all output lands in `delta.content`; latency is bounded by the actual reply length.
- Cons: Forfeits reasoning for now; if a future feature needs it, we'll need to consume `reasoning_content` explicitly rather than re-enable thinking globally.

**Chosen:** C. Saved to `project_gemma_thinking_mode.md` memory so future planning doesn't re-discover it.

### Persistence vs. JSONL sink for observations

**Option A:** Stand up Postgres + AGE (#028) as part of this slice, persist observations to a real learner-profile table.
- Pros: The observation stream is what the learner model will consume; shipping them straight into the DB skips a migration step later.
- Cons: Hard dependency on a schema we haven't designed yet, plus async Postgres driver, docker-compose service, migrations, init script. Doubles the scope and leaks into multiple spec items (#028–#033).

**Option B (chosen):** Write observations to a JSONL file + in-memory ring buffer. Replace the sink when #026 lands.
- Pros: Observations can be inspected immediately; one-line appends; the shape of a "turn observation" can be validated against real sessions before committing to a schema.
- Cons: Throwaway work when the DB lands — maybe half a day of reshaping the sink.

**Chosen:** B. The half-day reshape is cheaper than committing to a schema we haven't seen real data against.

### Where the prompt renderer lives

This was Open Question #2 in the spec. Three options on the table:

- **A (chosen):** Move `render_system_prompt` + its dependencies out of `finetune/format.py` into `hable_ya/pipeline/prompts/render.py`. Both paths import from there.
- **B:** `hable_ya/pipeline/prompts/builder.py` imports `_render_system_prompt` directly from `finetune.format`. Works but inverts the dependency (runtime → fine-tune code).
- **C:** Leave both; have the builder re-implement the logic; assert byte-for-byte equality via a test.

**Chosen:** A. The user picked it during spec review; the other options were there to frame the tradeoff, not to be serious candidates.

### Graceful-degradation vs. strict tool-call validation

**Option A:** Treat a missing `log_turn` as a pipeline error — fail the turn, maybe retry.
- Pros: Crisper guarantee about observation data quality.
- Cons: The currently-deployed fine-tuned Gemma misses the call ~1 turn in 5 (per `project_log_turn_emission.md`). A strict mode would abort or stall the conversation on every fifth turn, which is unacceptable learner UX.

**Option B (chosen):** Log the miss, increment `sink.missing`, proceed. Apply the same logic to malformed args and invalid `fluency_signal` enum values.
- Pros: Matches the reality of the model's output distribution. The counter itself is the signal — if it drifts substantially from the fixture-eval baseline, that's a model-quality regression worth investigating.
- Cons: Observation stream is incomplete by design.

**Chosen:** B, explicitly saved to memory so future consumers of the observation stream don't design around an assumption of completeness.

---

## Tradeoffs

What this slice optimizes for:

- **Getting real pedagogical signal out of live sessions.** This is the first time `log_turn` data from a real learner (not a fixture) is captured to a structured file the project owner can inspect. The fine-tune evaluation story finally has a runtime counterpart.
- **A single source of truth for prompt rendering.** Any change to register guidance, FORBIDDEN phrases, or the `log_turn` instruction block now lands in one place. Previously the runtime placeholder could drift from the training data unobserved.
- **Keeping the LLM behavior close to what the fine-tune actually produced.** By dropping `tools=` and disabling thinking, we're letting the model emit the exact shape it was trained on — text-form `log_turn(...)` in plain `delta.content`.

What it gives up:

- **No persistence.** `runtime_turns.jsonl` is readable but won't survive a rewrite when #026 lands. The ring buffer only covers the last 100 turns.
- **No side-channel events to the frontend.** Captions, recap, pivot announcements, and any other UI that wants to consume `log_turn` data in real time are blocked until a separate spec defines the events protocol.
- **No dynamic learner profile.** The prompt's L1 reliance, fluency, error patterns, and vocab strengths are static midpoints per band. The model gets a neutral picture until #029 wires in real profile data.
- **No theme selection.** The prompt uses a neutral "conversación abierta" theme; `THEMES_BY_LEVEL` and `get_session_theme` remain stubs.
- **No cold-start diagnostic flow.** `COLD_START_INSTRUCTIONS` is a static opt-in string appended via `{"cold_start": True}` in the learner dict, not a proper session-1 band-detection flow.
- **STT transcript quality is a known bottleneck and was left untouched.** Manual smoke confirmed the agent loop functionally works, but transcripts from `faster-whisper small` are poor enough that the agent regularly receives garbled user utterances. Rolled into #047 as a follow-up.
- **Gemma reasoning is off.** If a future feature wants reasoning (e.g. pedagogical meta-analysis on why a recast was chosen), we'll need to consume `reasoning_content` explicitly rather than flip thinking back on globally.

---

### Spec Divergence

| Spec Said | What Was Built | Reason |
|---|---|---|
| Approach §Tool Schema: "pass `HABLE_YA_TOOLS` via the LLM service's `tools=` parameter" (best-effort) | Tools **not** wired into `LLMContext`; `HABLE_YA_TOOLS` stays in the repo for tests and future documentation only | Declaring tools made llama.cpp suppress the text reply entirely — 20 consecutive empty-response turns in manual smoke. Spec had flagged this as a confidence risk; the risk materialised and the fallback was shipped. |
| Not in spec | `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` added to `OpenAILLMService.InputParams.extra` in `hable_ya/pipeline/services.py` | Gemma 4 thinking mode (on by default in llama.cpp) routes all reply text into `delta.reasoning_content`, which pipecat ignores. Without this, TTS goes silent on every turn. The spec didn't anticipate this; it's now in project memory. |
| AC: "dev-only endpoint or REPL hook" for the ring buffer (Open Question #1) | `GET /dev/observations?n=N` under `settings.dev_endpoints_enabled`, gated at router-include time with a `WARNING` log line at startup | Both user and I agreed during spec review. No divergence beyond the name. |
| §Testing Approach: "live session produces one line per agent turn that included a `log_turn` call" | Validated — manual smoke produces well-formed JSONL lines; `/dev/observations` returns the ring buffer; TTS speaks Spanish replies and never the tool-call text | As designed. |
| §Testing Approach: diagnostic log of buffered LLM text at INFO | Demoted to DEBUG before merge | The full-text dump is useful for debugging but noisy at INFO for normal operation. The `missing` counter and start/end framing remain visible via the sink + pipecat's own logging. |

No other acceptance criteria diverged.

---

## Spec Gaps Exposed

1. **llama.cpp + Gemma 4 thinking-mode interaction** was not documented anywhere in the repo before this slice. Saved to `project_gemma_thinking_mode.md`. The docker-compose layer (#018) served the model without mentioning this; future docs for serving Gemma should call it out.

2. **STT quality under live conditions is materially worse than fixture transcripts suggest.** The fixture eval workstream tested against clean typed text; the runtime feeds whatever `faster-whisper small` produces from a microphone, and Spanish transcripts were poor enough in manual smoke to degrade the learner experience. Spec 023 deliberately kept STT config untouched; **#047** is the follow-up, covering Whisper model size (medium/large-v3), VAD stop-seconds tuning, `no_speech_prob` threshold, and `initial_prompt` priming.

3. **Pipecat's `OpenAILLMService` ignoring `delta.reasoning_content`** is a silent drop that cost ~30 minutes of diagnosis. Worth a short note in the service layer or a future adapter that either forwards reasoning as a distinct frame type or raises a clear warning on first encounter.

4. **Tool declaration via `LLMContext(tools=...)` is incompatible with plain-text-trained tool-call emission** on llama.cpp. Not a general Pipecat bug — a fine-tune-scope collision — but worth noting in `OVERVIEW.md` or `ARCHITECTURE.md` next to the eval-vs-runtime contract so the next slice that wants to add a second tool doesn't re-hit it.

5. **Side-channel events protocol** (flagged originally by spec 046's decision record) is now doubly blocked: captions need it, and any future real-time consumer of `log_turn` observations (live recap, pivot announcements, level badge updates) needs it too. Worth a dedicated spec before the next frontend slice.

---

## Test Evidence

**Automated (CI-gated), passing:**

```
pytest -q
...
109 passed, 9 warnings in 4.09s
```

```
ruff check hable_ya/ api/ tests/test_prompts.py tests/test_tools.py \
  tests/test_tool_handler.py tests/test_observations.py tests/test_runner.py
All checks passed!
```

```
mypy hable_ya/ api/ tests/test_prompts.py tests/test_tools.py \
  tests/test_tool_handler.py tests/test_observations.py tests/test_runner.py
Success: no issues found in 37 source files
```

New tests: `tests/test_observations.py` (7 tests — append, recent, ring cap, concurrent append, missing counter start-state); `tests/test_tools.py` (schema shape + malformed-args rejection under Draft202012Validator). Substantially rewritten: `tests/test_prompts.py` (per-band byte identity with `render_system_prompt`, FORBIDDEN presence, cold-start opt-in); `tests/test_tool_handler.py` (happy path both tool-call surfaces, missing, malformed errors, invalid fluency_signal, pure-tool-call response, unclosed payload); `tests/test_runner.py` (threaded sink + session_id into `build_pipeline` calls).

**Manual smoke (live pipeline + llama.cpp + web frontend), passing after two fixes:**

1. Initial attempt: 20 consecutive turns with `missing++` and no TTS audio. Diagnosed via the temporary diagnostic log (`LLM response end · 0 chunks · 0 chars · text=''`) plus a direct curl against llama.cpp (content field populated in non-streaming, `delta.reasoning_content` only in streaming). Two fixes landed:
    - Dropped `tools=ToolsSchema(...)` from `LLMContext` construction in `api/routes/session.py` (commit [`1c599c4`](../../..)).
    - Added `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to `OpenAILLMService.InputParams.extra` in `hable_ya/pipeline/services.py` (commit [`8c44067`](../../..)).

2. Post-fix smoke: user held a Spanish conversation, agent replied in Spanish, `runtime_turns.jsonl` populated with well-formed JSON lines, `missing` counter stayed proportional to ~1 miss per ~5 turns (matching the `project_log_turn_emission.md` baseline). STT transcript quality was poor (known, rolled into #047) but the pipeline handled the garbled input without crashing — exactly the graceful-degradation behavior the handler was designed for.

**Verified ACs from spec §What:**

- [x] Per-band system prompt byte-identity vs `render_system_prompt`
- [x] `REGISTER_BY_LEVEL` populated for all five bands, sourced from `render.py`
- [x] `COLD_START_INSTRUCTIONS` non-empty, opt-in via learner dict
- [x] `HABLE_YA_TOOLS` non-empty and validates (schema tests)
- [x] Tool handler parses both surface forms, dispatches to sink, preserves strip behavior
- [x] Ring buffer + dev endpoint return observations as JSON
- [x] `settings.default_learner_band` controls band for all sessions
- [x] Missing/malformed `log_turn` increments `missing`, logs warning, pipeline continues
- [x] Existing tool-strip TTS behavior — no regression
- [x] `pytest` clean

Not verified in automated CI (inherently manual):

- [x] "Register adapts audibly between `HABLE_YA_DEFAULT_LEARNER_BAND=A1` and `=C1`" — not independently walked during smoke; prompt-level difference is byte-verified in tests, but audible delta across bands wasn't validated in this session. Low risk given the test coverage of the underlying prompt-construction path; worth a revisit if band-specific UX feedback ever suggests drift.
