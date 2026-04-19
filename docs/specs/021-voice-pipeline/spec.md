# Spec: Voice Agent Pipeline (WebSocket + Pipecat + faster-whisper + piper-tts)

| Field | Value |
|---|---|
| id | 021 |
| status | approved |
| created | 2026-04-19 |
| covers roadmap | #021 (WebSocket `/ws/session`), #022 (Pipecat composition) |
| reference | `../comprende-ya-old/voice-agent/voice_agent_pipecat.py` (working Pipecat pipeline against a llama.cpp-served LLM; used as the blueprint for import paths, topology, and warmup pattern) |

---

## Why

`hable-ya`'s whole product story is a voice-in / voice-out Spanish conversational partner. Today the runtime side is entirely stubbed: `api/routes/session.py` raises `NotImplementedError`, `hable-ya/pipeline/runner.py` is empty, and there's no way to actually talk to the fine-tuned model. All validation to date has been through the offline eval harness scoring fixture conversations. We need an end-to-end voice loop so the model is exercisable as a product, not just as a checkpoint, and so that every subsequent runtime feature (learner state, tool handling, cold-start, knowledge graph) has something to hang off.

### Consumer Impact

- **End user (learner):** gets an actual working voice session — opens a client, speaks Spanish, hears the agent respond in Spanish. This is the first feature that produces a runtime artifact the learner can touch.
- **Project owner (researcher/developer):** gets a latency- and quality-testable voice loop against the fine-tuned model. Enables subjective validation that the model behaves as the eval scores suggest, and provides the substrate for the future agent-eval loop (#034–#036), learner model (#029–#033), and tool integration (#025–#027).
- **Downstream features:** the web frontend (#046), tool handler (#025), turn observer (#026), learner profile (#029), and on-device deployment (#043) all require this pipeline to exist.

### Roadmap Fit

This is the foundation of the runtime workstream. Nothing in the runtime half of the roadmap can be validated end-to-end until this exists.

Dependencies:
- Upstream (needed before this): none strictly required. A minimum-viable system prompt string is inlined here to avoid blocking on #023/#024; a placeholder learner profile is used instead of a real DB (#028) or learner module (#029).
- Downstream (unblocked by this): #025 (tool handler), #026 (turn observer), #029 (learner profile), #034–#036 (agent eval), #046 (web frontend).

Deliberately deferred from this spec (covered by later specs):
- Pedagogical system prompt content (#023, #024) — this spec uses a minimal placeholder prompt sufficient to get a coherent Spanish reply.
- Tool-call execution against learner state (#025). The processor in this spec recognizes and strips `[TOOL_CALL: log_turn]{...}` blocks from the spoken text but does not persist them.
- Turn observer persistence (#026) — out of scope; no DB writes in v1.
- `HABLE_YA_TOOLS` schema (#027).
- Postgres + AGE setup (#028) and learner profile (#029) — out of scope; this spec uses an in-memory placeholder profile.
- Web frontend (#046) — this spec lands the server-side endpoint and documents its protocol; a browser client is a separate feature.

---

## What

### Acceptance Criteria

From the consumer's perspective, with the llama.cpp server running and a WebSocket client that streams microphone audio:

- [ ] A client can open a WebSocket to `/ws/session` and conduct a multi-turn spoken Spanish conversation with the agent.
- [ ] The agent's first spoken utterance occurs after the client finishes speaking (VAD-triggered end-of-turn), not while the user is mid-sentence.
- [ ] The agent's response audio is intelligible Spanish produced by piper-tts and streamed back over the same WebSocket.
- [ ] Any `[TOOL_CALL: ...]{...}` block emitted by the LLM is stripped from the text passed to TTS (the user does not hear tool-call syntax).
- [ ] Closing the WebSocket tears down the pipeline cleanly (no zombie Pipecat tasks, llama.cpp connection released).
- [ ] An error calling the llama.cpp endpoint (timeout, 5xx) ends the session with a clean close rather than crashing the FastAPI worker.
- [ ] Up to 4 concurrent sessions can run without errors (matches `--parallel 4` on llama.cpp), subject to VRAM headroom.
- [ ] First-token latency (end-of-user-turn to first TTS audio chunk) is recorded and measurable, with a p50 target of ≤ 1.5s on the dev box (tighter than the earlier 2.0s budget given CUDA Whisper). Document the number in the decision record; this is a measurement criterion, not a hard gate.
- [ ] `uvicorn api.main:app` starts without errors on a fresh checkout after `uv sync --all-extras`.
- [ ] `GET /health` returns 503 during warmup and 200 once the llama.cpp endpoint responds to the warmup ping.
- [ ] CUDA Whisper loads without cuBLAS/cuDNN errors (the `cuda_bootstrap` shim is effective).

### Non-Goals

- No learner-specific adaptation. The system prompt is a fixed placeholder that asks the model to hold a Spanish conversation at a moderate register. Per-band register, cold-start diagnostic, learner profile context — all out of scope.
- No tool execution. Tool-call blocks are *removed from speech* but not parsed or dispatched.
- No persistence. No DB writes, no session logs to disk. In-memory only.
- No authentication, rate limiting, or tenant isolation. Single-tenant per OVERVIEW.
- No browser client in this spec. The deliverable is the server; a minimal test harness (CLI-based audio client or pytest fixture) is acceptable for validation.
- No production deployment path. Dev-only on the docker-compose box.
- No multi-language routing. Spanish-only TTS voice.

### Resolved

1. **Audio transport:** `FastAPIWebsocketTransport` from `pipecat.transports.websocket.fastapi`. 16 kHz, mono, both directions. Pipecat's default serializer — no custom serializer in v1.
2. **Piper voice:** `es_ES-carlfm-x_low` — castellano, "x_low" quantization for minimal latency. Higher-quality variants (`_low`, `_medium`) available as config knobs if latency budget permits.
3. **Whisper device and size:** default to **CUDA + float16 + `small`** to prioritize latency. `medium` is the next step up if WER on learner Spanish is unacceptable; CPU is the fallback if VRAM pressure becomes an issue. The NVIDIA lib-path shim from the reference (adds `nvidia.cublas.lib` / `nvidia.cudnn.lib` dirs to `LD_LIBRARY_PATH` and re-execs) is required for CUDA Whisper to find cuBLAS/cuDNN.
4. **Minimum system prompt:** inline `PLACEHOLDER_SYSTEM_PROMPT` in `pipeline/prompts/builder.py` that (a) says "respond in Spanish", (b) forbids explicit correction, (c) asks the model to emit `[TOOL_CALL: log_turn]{...}` after each response. Flagged for replacement by #023.
5. **Turn-taking:** Smart Turn V3 on top of Silero VAD — `LocalSmartTurnAnalyzerV3(SmartTurnParams(stop_secs=4.0))` + `SileroVADAnalyzer(sample_rate=16000, stop_secs=0.5)` wired via `LLMUserAggregatorParams.user_turn_strategies`. Tolerates L2-learner mid-utterance pauses.
6. **Max LLM completion tokens:** 150 (configurable via `config.llm_max_tokens`). Matches voice-length expectations and the `sentence_count_ok` pedagogical heuristic.
7. **Side-channel WS messages:** not in v1. Pipecat default serializer only. Custom serializer is the extension point when the web frontend (#046) needs transcripts or tool-call events over the same socket.
8. **End-of-session signaling:** WS close only. No "done" control message. Known gap — revisit if the web frontend (#046) needs graceful-drain semantics.
9. **Service lifecycle:** STT/LLM/TTS loaded once in a FastAPI `lifespan` context and shared across WS sessions. Warmup pings llama.cpp with a 1-token request on startup (reference's retry pattern). `GET /health` returns 503 during warmup, 200 once ready.

---

## How

### Approach

The reference at `../comprende-ya-old/voice-agent/voice_agent_pipecat.py` is a working implementation of the same class of pipeline. We follow its structure closely, with these intentional deltas: (1) Gemma 4 E4B finetuned GGUF instead of Llama-3.2-3B; (2) castellano (ES) piper voice; (3) no MCP learner-model integration yet — placeholder profile; (4) add a `HableYaToolHandler` processor that strips `[TOOL_CALL: ...]{...}` blocks from LLM text before TTS; (5) no `SessionInterceptor`/`TranscriptionObserver` equivalents in this spec — a minimal stdout `HableYaTurnObserver` replaces them, with the full observer arriving in #026.

**New / touched modules:**

```
api/main.py                           add lifespan for warmup + load services once
api/routes/session.py                 implement WebSocket endpoint (WS → PipelineTask)
hable-ya/pipeline/runner.py           build_pipeline_task() — composes topology
hable-ya/pipeline/prompts/builder.py  PLACEHOLDER_SYSTEM_PROMPT + build_system_prompt()
hable-ya/pipeline/services.py         module-scope STT/LLM/TTS singletons (new file)
hable-ya/pipeline/processors/
    tool_handler.py                   strip [TOOL_CALL: ...]{...} from text frames
    turn_observer.py                  stdout logger (full impl in #026)
hable-ya/cuda_bootstrap.py            NVIDIA lib-path shim (new file, imported first)
hable-ya/config.py                    add fields — see below
```

**Pipeline topology (composed in `runner.py`), matching the reference:**

```
transport.input()                     FastAPIWebsocketTransport (16 kHz mono WS audio in)
  └─► stt_service                     WhisperSTTService (cuda/float16/small, language=ES)
       └─► HableYaTurnObserver        observes transcription frames (stdout log)
            └─► aggregators.user()    LLMContextAggregatorPair.user()
                                      — user_turn_strategies = Smart Turn V3 + SileroVAD
                 └─► llm_service      OpenAILLMService (base_url=config.llama_cpp_url,
                                      model=config.llm_model_name, max_tokens=150)
                      └─► tts_service PiperTTSService (es_ES-carlfm-x_low, 16 kHz)
                           └─► HableYaToolHandler   strip [TOOL_CALL:...]{...}
                                └─► transport.output()   audio out over WS
                                     └─► aggregators.assistant()   closes the turn
```

Note on ordering: the assistant aggregator goes *after* `transport.output()` (as in the reference) — the assistant turn is recorded into the context after the audio ships. The tool-handler runs *after* TTS in the reference's style for interceptors, but for hable-ya it should run *before* TTS so `[TOOL_CALL: ...]{...}` is never synthesized to speech. **This is the one topology divergence from the reference.**

**Shared services (`hable-ya/pipeline/services.py`, loaded once in `api/main.py` lifespan):**

```python
stt_service = WhisperSTTService(
    model=WhisperModel.SMALL,              # start small; config-driven
    device="cuda",
    compute_type="float16",
    no_speech_prob=0.6,
    language=Language.ES,
)
llm_service = OpenAILLMService(
    model=config.llm_model_name,
    base_url=config.llama_cpp_url,
    api_key="not-needed",
    retry_timeout_secs=30.0,
    retry_on_timeout=True,
    params=OpenAILLMService.InputParams(
        temperature=0.7, top_p=0.9, max_completion_tokens=150,
    ),
)
tts_service = PiperTTSService(
    voice_id="es_ES-carlfm-x_low",
    download_dir=config.piper_model_dir,
    sample_rate=16000,
)
```

**FastAPI endpoint (`api/routes/session.py`):**
- Accept WS upgrade.
- Build a fresh `LLMContext` with the placeholder system prompt (per-connection — the services are shared, the context is not).
- Build the `FastAPIWebsocketTransport` around the WS with 16 kHz mono in/out.
- Wire aggregators with `SileroVADAnalyzer(sample_rate=16000, stop_secs=0.5)` and `LocalSmartTurnAnalyzerV3(SmartTurnParams(stop_secs=4.0))`.
- Compose the `Pipeline`, wrap in `PipelineTask(allow_interruptions=True, enable_metrics=True)`, run to completion; WS close ends the task cleanly.

**Lifespan / warmup (`api/main.py`):**
- Import `hable-ya/cuda_bootstrap.py` first (shim sets `LD_LIBRARY_PATH` and re-execs if needed — exact pattern from reference lines 14–43).
- On `lifespan` startup: load services, send a 1-token ping to llama.cpp with retry (reference warmup() pattern), flip a `_ready` flag.
- `GET /health` returns 503 with `{"status": "warming_up"}` until ready.

**Placeholder learner context:**
- Constant `DEFAULT_LEARNER = {"band": "A2", "learner_id": "placeholder"}` in `runner.py`; fed into `build_system_prompt()`. Replaced by real profile lookup when #029 lands.

**Configuration additions (`hable-ya/config.py`):**
- `whisper_model: str = "small"` — `tiny|base|small|medium|large-v3`
- `whisper_device: str = "cuda"` — `cuda|cpu`
- `whisper_compute_type: str = "float16"` — `float16|int8|float32`
- `piper_voice: str = "es_ES-carlfm-x_low"`
- `piper_model_dir: Path = Path("~/piper_models").expanduser()`
- `llm_model_name: str = "gemma-4-e4b-finetuned"` — name as registered with llama.cpp
- `llm_max_tokens: int = 150`
- `smart_turn_stop_secs: float = 4.0`
- `vad_stop_secs: float = 0.5`
- `session_timeout_s: int | None = None` — `None` = no cap (match reference `idle_timeout_secs=None`); keep as config knob

### Confidence

**Level:** High.

**Rationale:**
- The reference implementation at `../comprende-ya-old/voice-agent/voice_agent_pipecat.py` is a working Pipecat pipeline against a llama.cpp-served LLM on the same pinned version line (`pipecat-ai>=0.0.103`). All pipecat import paths, service constructor signatures, aggregator wiring, VAD-on-aggregator convention, CUDA Whisper + float16 latency posture, Piper castellano voice, and warmup pattern are already proven there.
- The OpenAI-compatible client against llama.cpp is already used in `eval/run_eval.py`.
- The `[TOOL_CALL: ...]{...}` format is parsed by `eval/scoring/turn.py:parse_tool_calls`; the tool-handler can import that parser rather than duplicating a regex.
- Remaining uncertainties are small-surface and can be handled inside implementation without re-specking:
  - Whether Smart Turn V3's `stop_secs=4.0` works as well for Spanish learner speech as it does for native English; may need tuning.
  - Whether `small` Whisper WER is acceptable on non-native Spanish; `medium` is the fallback.
  - Exact VRAM headroom with Gemma 4 E4B Q8_0 (`--n-gpu-layers 99`) + Whisper-small/float16 co-resident — needs measurement but the reference proves a similar co-residency profile works.

**Validate during implementation (not blocking spec approval):**
1. Record p50/p95 first-token latency on the dev box, per the acceptance criterion.
2. `nvidia-smi` during a live session to confirm co-residency headroom; if VRAM-pressured, first downgrade Whisper to `base`, then fall back to `compute_type="int8"`, then CPU as last resort.
3. Run a short subjective listening pass on `carlfm-x_low` output — if intelligibility is poor at `x_low`, try `_low` or `_medium` variants before shipping.

### Key Decisions

- **Follow the reference pipeline's structure.** `../comprende-ya-old/voice-agent/voice_agent_pipecat.py` is a proven topology and warmup pattern on the same pipecat version line. We adopt its imports, aggregator wiring, VAD-on-aggregator convention, and module-scope service sharing with deliberate deltas (tool-handler before TTS, castellano voice, Gemma 4 E4B).
- **`FastAPIWebsocketTransport` with default serializer.** Confirmed from reference; avoids a custom serializer until the web frontend (#046) needs structured side-channel messages.
- **Whisper on CUDA + float16, `small` model, sharing GPU with llama.cpp.** Latency-first posture. The reference co-hosts Whisper `medium` + float16 with a 3B-class GGUF on one GPU; our LLM is larger (Gemma 4 E4B Q8_0, 99 GPU layers) so we start a size below (`small`). All three levers — model size, compute_type, device — are config knobs so the fallback path is pure configuration.
- **Smart Turn V3 + Silero VAD for turn-taking.** Adopted from the reference; the natural-pause tolerance materially benefits L2 learners composing Spanish utterances mid-turn.
- **Tool-handler strips, does not parse.** Keeps this spec a pure plumbing change. The parser in `eval/scoring/turn.py` already knows the format; when #025 implements actual tool dispatch, it can import that parser rather than duplicating regex.
- **Tool-handler runs before TTS (diverging from the reference's interceptor-after-TTS style).** Ensures `[TOOL_CALL: ...]{...}` is never synthesized to speech.
- **Placeholder system prompt, clearly labeled.** Avoids blocking this spec on #023/#024 but must not become permanent — the constant is named `PLACEHOLDER_SYSTEM_PROMPT` and flagged so #023 knows where to replace it.
- **No DB in this spec.** Keeps the feature independently shippable and independently testable. The turn observer logs to stdout until #026 replaces it.
- **NVIDIA lib-path shim adopted wholesale.** The `_ensure_nvidia_lib_path()` re-exec pattern from the reference is required for CUDA Whisper; we land it as `hable-ya/cuda_bootstrap.py` and import it first in `api/main.py`.

### Testing Approach

**Unit tests (pytest, `tests/`):**
- `tests/test_tool_handler.py` *(new)* — cases for `HableYaToolHandler`'s text-stripping:
  - `[TOOL_CALL: log_turn]{...}` at end of response → stripped, leading text passed through.
  - No tool-call block → text passes through unchanged.
  - Multi-line response with tool-call block separated by blank line → only the block is stripped.
  - Malformed tool-call block (unclosed brace) → passed through unchanged (TTS would speak it; acceptable v1 behavior, to be tightened in #025).
- `tests/test_prompts.py` *(currently stubbed)* — add a case that `build_system_prompt(band="A2")` returns a non-empty string containing "español" and does not contain forbidden explicit-correction phrases drawn from `finetune/format.py`'s forbidden list.

**Integration test (manual, documented in decision record):**
- Start `docker compose up llama`, then `uvicorn api.main:app`.
- Confirm `/health` is 503 during warmup, then 200.
- Run a small CLI client (scripted as `scripts/voice_client.py` — a validation helper created alongside this feature) that streams a pre-recorded castellano WAV to the WS and saves the response audio to disk.
- Assertions: connection completes, response audio exists, response is non-silent, response duration is plausible (> 0.5s, < 15s), first-TTS-frame latency is recorded.

**VRAM co-residency check (manual):**
- During a live session, `nvidia-smi` should show both Gemma GGUF and Whisper-small loaded without OOM; document peak VRAM in the decision record.

**Concurrency smoke test (manual):**
- Run 4 instances of the CLI client in parallel against the same server; confirm all four complete, no errors logged, VRAM stays within budget.

**Turn-taking subjective check:**
- One recorded utterance containing a natural 2-second mid-sentence pause (e.g., "Ayer fui… [pause] al mercado"): confirm Smart Turn V3 does *not* end the turn at the pause. One recorded utterance with a clear 4-second end-of-turn pause: confirm turn is ended.

**Latency measurement:**
- `enable_metrics=True` on `PipelineTask` surfaces Pipecat's built-in metrics; capture end-of-user-turn → first-TTS-frame timings. Record p50/p95 over the integration test runs in the decision record.

**No automated audio-quality gate in this spec.** Subjective audio quality is out of scope; agent-eval (#034–#036) is the structured quality mechanism.
