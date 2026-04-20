# Spec: STT/TTS Quality Upgrade + Debug Latency Metric

| Field | Value      |
|---|------------|
| id | 048        |
| status | approved   |
| created | 2026-04-20 |

---

## Why

Live voice sessions currently suffer two quality issues and lack a clear latency signal:

1. **STT** uses faster-whisper `small`, which produces poor Spanish transcripts under live microphone conditions (roadmap #047). Garbled transcripts silently corrupt every downstream signal — the LLM replies to a sentence the learner didn't say, recasts miss, and `log_turn` records noise.
2. **TTS** uses Piper `es_ES-carlfm-x_low`, an x_low-quality voice chosen for throughput on earlier hardware. The synthesized voice sounds robotic enough to break the "natural conversational partner" design goal.
3. **Latency** is not measured end-to-end. Pipecat emits per-service TTFB metrics, but nothing aggregates "user stopped speaking → agent started speaking." Without that number, quality-vs-latency tradeoffs (e.g. medium Whisper, medium Piper) are judged by feel.

Stepping up both models one quality tier and adding an opt-in latency probe closes all three gaps with minimal surface change.

### Consumer Impact

- **End user (learner):** understandable TTS voice and transcripts that actually match what they said. Direct prerequisite for any session feeling like a conversation rather than a demo.
- **Researcher / developer:** a single number to optimize against when tuning models, VAD thresholds, or Gemma generation params. Debug-only so it doesn't pollute production logs or add overhead to shipped deployments.

### Roadmap Fit

- Subsumes #047 (STT quality investigation) with a concrete action rather than an investigation.
- Unblocks realistic manual testing of #023 agent-loop behavior — recasts and `log_turn` are only meaningful when the transcript is right.
- Prerequisite for #038 (latency benchmark script), which will want the same end-to-end measurement method reused outside a live session.

---

## What

### Acceptance Criteria

- [ ] Default STT model is faster-whisper `medium` (Spanish transcription quality visibly improves in a live session).
- [ ] Default TTS voice is a medium-quality Spanish Piper voice; output sounds noticeably more natural than `es_ES-carlfm-x_low` in A/B listening.
- [ ] When a debug flag is enabled, every agent turn logs one end-to-end latency number: milliseconds from user-speech-end (VAD / smart-turn stop) to agent-speech-start (first TTS audio frame out).
- [ ] When the debug flag is disabled (default), no latency instrumentation runs in the hot path — no timers, no logs, no added processors.
- [ ] Existing pytest suite still passes; no new test failures in `hable_ya/` lint/type scope.

### Non-Goals

- No change to Gemma / llama.cpp serving.
- No per-service latency breakdown (STT ms / LLM ms / TTS ms) — one end-to-end number only. Pipecat's built-in metrics already cover per-service TTFB for anyone who wants the breakdown.
- No latency budget enforcement, alerting, or persistence to the observation sink. Stdout/log line only.
- No dynamic voice switching per learner or per band. Single default voice.
- No GPU/CPU compute-type changes for Whisper beyond what's already configurable.
- No bundling the chosen Piper voice into the repo — it downloads to `piper_model_dir` on first use like the current voice does.

### Resolved Questions

- **Piper voice:** `es_ES-davefx-medium` (confirmed by user). Artifacts at https://huggingface.co/rhasspy/piper-voices/tree/main/es/es_ES/davefx/medium/; `PiperTTSService` downloads on first use into `piper_model_dir`.
- **Debug flag:** `HABLE_YA_LATENCY_DEBUG` (bool, default False). Independent of `dev_endpoints_enabled` so the latency probe can be toggled without exposing `/dev/*` routes.

---

## How

### Approach

Three narrow changes, each isolated to one module.

**1. STT model bump (`hable_ya/config.py`)**

Change the default of `whisper_model` from `"small"` to `"medium"`. No code change in `services.py` — it already does `WhisperModel[settings.whisper_model.upper()]`, and `MEDIUM` is a valid enum member (verified: `pipecat.services.whisper.stt.Model` exposes `TINY/BASE/SMALL/MEDIUM/LARGE/...`). Document the VRAM bump in the decision record.

**2. TTS voice bump (`hable_ya/config.py`)**

Change the default of `piper_voice` from `"es_ES-carlfm-x_low"` to `"es_ES-davefx-medium"`. `PiperTTSService(download_dir=...)` handles the model fetch on first use from the rhasspy/piper-voices HF repo. No code change in `services.py`.

**3. End-to-end latency probe (new processor, gated)**

Add a `LatencyProbe` frame processor in `hable_ya/pipeline/processors/latency_probe.py`:

- Stamps `time.perf_counter()` on `UserStoppedSpeakingFrame` (or `BotInterruptionFrame` equivalent — pick whichever Pipecat frame marks end-of-user-turn from the aggregator/smart-turn path).
- On the first `TTSAudioRawFrame` after each stamp, computes elapsed ms and logs at INFO under `hable_ya.latency`. Clears the stamp so only the first audio frame per turn logs.
- Inserted in `build_pipeline` **only when `settings.latency_debug` is True**. When False, the processor is not constructed and not added to the pipeline list — zero hot-path cost.

Add `latency_debug: bool = False` to `Settings` (env var `HABLE_YA_LATENCY_DEBUG`).

### Confidence

**Level:** High

**Rationale:**
- Config-only defaults for STT/TTS; both Whisper `MEDIUM` and the Piper medium voice format are supported by the libraries already in use.
- Pipecat frame types for user-turn-end and TTS-first-audio are stable API used elsewhere in the repo (`turn_observer.py` already consumes `TranscriptionFrame`, `LLMFullResponseEndFrame`).
- Gating by constructor-time flag avoids any production overhead concerns.
- Remaining risk (which specific Piper voice sounds best, exact frame names for the probe) is resolvable during implementation, not before.

### Key Decisions

- **One number, not a breakdown.** Per-service TTFB is already in Pipecat's metrics (`enable_metrics=True` is on in `build_pipeline_task`). Adding a duplicate breakdown here would sprawl; end-to-end is the number actually missing.
- **Gate at pipeline-construction time, not at frame-dispatch time.** A disabled feature flag that still installs a processor still runs `process_frame` on every frame. Construct-time gating makes the production path literally identical to today.
- **Bump defaults rather than add config tiers.** No `stt_quality: low/med/high` abstraction — the user wants medium, set medium. Overridable via env var like any other setting if someone needs to revert.

### Testing Approach

- **Manual live-session test.** Start app + llama.cpp, open web frontend (#046), run a 2–3 minute Spanish conversation. Verify: transcripts in `hable_ya.turns` logs are readable and match speech; TTS is audibly better; latency log line appears once per turn when `HABLE_YA_LATENCY_DEBUG=true`, zero lines when unset.
- **Unit test for the probe (optional, low value).** A synthetic frame sequence (UserStoppedSpeakingFrame → TTSAudioRawFrame → TTSAudioRawFrame) through `LatencyProbe` should log exactly once. Skip if the frame types require deep Pipecat mocking — manual verification is the real test.
- **CI.** Existing `ruff`/`mypy`/`pytest` scoped to `hable_ya/` must stay green. New file goes under that scope.
