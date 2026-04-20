# Decision Record: STT/TTS Quality Upgrade + Debug Latency Metric

| Field | Value |
|---|---|
| id | 048 |
| status | implemented |
| created | 2026-04-20 |
| spec | [spec.md](./spec.md) |

---

## Context

Live voice sessions on `main` had three identified problems: faster-whisper `small` produced unreliable Spanish transcripts under mic conditions (roadmap #047), Piper `es_ES-carlfm-x_low` sounded robotic enough to undercut the "natural conversational partner" design goal, and no end-to-end latency number existed for tuning decisions (Pipecat's built-in metrics emit per-service TTFB, but nothing aggregates user-stop → bot-start).

Two discoveries during implementation shaped the result:

1. **Pipecat ships `UserBotLatencyObserver` out of the box** (`pipecat/observers/user_bot_latency_observer.py` in v0.0.108). It measures exactly the interval the spec calls for (`VADUserStoppedSpeakingFrame` → `BotStartedSpeakingFrame`) and emits `on_latency_measured(observer, latency_seconds)`. This obsoleted the spec's sketched `LatencyProbe` FrameProcessor — a custom processor would have duplicated the canonical implementation and sat in the frame chain rather than outside it.
2. **User acceptance testing confirmed TTS quality improved noticeably** with `es_ES-davefx-medium`. STT with faster-whisper `medium` is better than `small` but still imperfect on live Spanish audio — the user explicitly accepted this as the practical ceiling without additional work (no VAD tuning, no `initial_prompt` priming, no larger Whisper model). That acceptance is what closes roadmap #047 at this stage rather than leaving it open for further investigation.

## Decision

Flipped two runtime defaults in `hable_ya/config.py` and attached Pipecat's built-in `UserBotLatencyObserver` in `hable_ya/pipeline/runner.py` behind a new `HABLE_YA_LATENCY_DEBUG` flag. Net diff: `config.py` +3/-2, `runner.py` +18/-0, no new files. When the debug flag is off (default), the `PipelineTask` is constructed with `observers=None` — production path is byte-identical to before.

- `whisper_model`: `"small"` → `"medium"`
- `piper_voice`: `"es_ES-carlfm-x_low"` → `"es_ES-davefx-medium"`
- `latency_debug: bool = False` (new, env `HABLE_YA_LATENCY_DEBUG`)
- Latency logger at `hable_ya.latency`, one line per agent turn: `end_to_end_ms=<n>`.

---

## Alternatives Considered

### Latency measurement mechanism

**Option A: Custom `LatencyProbe` FrameProcessor (the spec's original sketch)**
- Pros: Full control over which frame pair bounds the measurement; could read `VADUserStoppedSpeakingFrame.timestamp - stop_secs` directly for a slightly more precise "actual silence" anchor than `time.time()` on `UserStoppedSpeakingFrame`.
- Cons: New file + new tests; sits inside the frame chain so "zero cost when off" requires construct-time gating anyway; reinvents behavior Pipecat ships.

**Option B: Pipecat's built-in `UserBotLatencyObserver` (chosen)**
- Pros: Zero custom code beyond a 4-line event handler; observers attach at the `PipelineTask` boundary and never enter the frame chain; Pipecat upstream maintains the measurement semantics; the same observer also emits `on_latency_breakdown` with per-service TTFB if we ever need to opt back into that.
- Cons: We take a dependency on a semi-public Pipecat API that could change shape across versions; the observer's "start" anchor is `VADUserStoppedSpeakingFrame.timestamp - stop_secs` (matches spec intent) but the event fires on `BotStartedSpeakingFrame`, not strictly on the first `TTSAudioRawFrame` — these are close but not identical in all pipeline topologies.

**Chosen: Option B.** The "reinvent what Pipecat ships" cost outweighs the small precision gap, and the API is stable enough in 0.0.108 to justify the dependency.

### How to gate the debug flag

**Option A: Install the observer unconditionally and check the flag inside the event handler**
- Pros: Simple; one code path.
- Cons: The observer still runs its full `on_push_frame` state machine on every frame in production; defeats the "no instrumentation in the hot path" acceptance criterion.

**Option B: Construct-time gate — only build the observer when the flag is on, pass `observers=None` otherwise (chosen)**
- Pros: Production `PipelineTask` is literally the same object graph as before the change; no branching on the hot path.
- Cons: Slightly more code at construction time; a restart is required to toggle the flag (acceptable for a debug-only concern).

**Chosen: Option B.** Matches the acceptance criterion exactly.

### Whisper model tier

**Option A: `medium` (chosen)** — one step up from `small`; ~769M params, fits alongside Gemma on the dev GPU in fp16.

**Option B: `large-v3-turbo`** — higher quality but larger VRAM footprint; would compete with Gemma for GPU memory.

**Option C: Keep `small`, invest in VAD tuning and `initial_prompt` priming** — roadmap #047's original framing.

**Chosen: A.** User accepted medium's output quality as the practical ceiling for this round; deferring B (larger model) and C (tuning) as future options if live-session quality becomes blocking again.

### Piper voice

Four medium-quality Spanish candidates existed on the rhasspy/piper-voices HF repo (`davefx`, `sharvard`, and two LatAm voices). The user picked `es_ES-davefx-medium` directly during spec review rather than running a listen-off — acceptable because all candidates are the same quality tier and taste is the deciding factor.

---

## Tradeoffs

- **Precision vs. simplicity in latency measurement.** Using `BotStartedSpeakingFrame` as the end anchor (rather than the first `TTSAudioRawFrame`) gives up a small amount of precision for a large simplification. The gap is bounded by how quickly Pipecat fires `BotStartedSpeakingFrame` after the first audio chunk; in the current pipeline that's effectively one event-loop hop.
- **VRAM for transcription quality.** Whisper medium adds roughly 1.5 GB of VRAM versus `small`. On the current dev GPU that coexists fine with Gemma-4-E4B Q8_0; on smaller devices (the hypothetical on-device deploy target, roadmap #043) this may need to be revisited.
- **STT quality is "good enough for now", not "good."** We accepted that medium faster-whisper on live Spanish mic input still produces occasional bad transcripts. This is a conscious ceiling, not a bug — closing #047 records the acceptance. If agent-loop evaluation (#034–#036) later shows transcript noise is the dominant failure mode, the reopening path is clear: try `large-v3-turbo`, then VAD tuning, then `initial_prompt` priming.
- **Production code now imports a Pipecat observer module even when the flag is off.** Negligible — the import is cheap and the class is never instantiated. Worth noting only because "production path identical" is aspirational; the import-graph delta is real but practically zero-cost.

### Spec Divergence

| Spec Said | What Was Built | Reason |
|---|---|---|
| New file `hable_ya/pipeline/processors/latency_probe.py` implementing a custom `LatencyProbe` FrameProcessor that stamps on `UserStoppedSpeakingFrame` and logs on first `TTSAudioRawFrame` | Four-line event handler inside `build_pipeline_task` that attaches Pipecat's built-in `UserBotLatencyObserver` | Discovered Pipecat 0.0.108 ships an equivalent observer; reusing it is strictly simpler and sits outside the frame chain (a stronger form of "zero hot-path cost" than a construct-gated processor) |
| Latency measured from `UserStoppedSpeakingFrame` → first `TTSAudioRawFrame` | Measured from `VADUserStoppedSpeakingFrame.timestamp - stop_secs` → `BotStartedSpeakingFrame` | The observer's anchors are Pipecat-canonical; the drift from the spec's exact wording is sub-100ms and doesn't affect the use case (tuning-by-single-number) |
| Open question: pick Piper voice by 30-second listen test | User picked `es_ES-davefx-medium` during spec review before implementation | Resolved before coding began; no listen-off needed |

Acceptance criteria, reviewed post-implementation:
- ✅ Default STT is faster-whisper `medium`.
- ✅ Default TTS is a medium-quality Spanish Piper voice; user confirmed audibly more natural.
- ✅ Latency logs one number per agent turn when flag is on.
- ✅ Flag off: `observers=None`, zero instrumentation.
- ✅ Existing pytest suite (109 tests) passes; scoped ruff + mypy pass.

---

## Spec Gaps Exposed

- **Roadmap #047 framing was broader than this spec resolved.** The roadmap entry listed three investigation axes (Whisper size, VAD tuning, `initial_prompt` priming). This spec addressed only the first. Marking #047 `implemented` alongside #048 is a judgment call — the investigation was closed by accepting the current quality ceiling, not by exhausting the three axes. If live-session quality regresses, reopen with a new spec covering VAD and priming rather than editing this one.
- **No documented VRAM budget anywhere in the repo.** Bumping Whisper to medium assumes the dev GPU has headroom; nothing in `ARCHITECTURE.md` or `OVERVIEW.md` states the target VRAM envelope. Candidate for #043 (on-device deployment target) to pin down.
- **`hable_ya.latency` is a new log namespace.** No central log-namespace convention doc exists (existing ones: `hable_ya.turns`, `hable_ya.pipeline.services`). Not a blocker, but a future doc pass on log namespaces would help.

---

## Test Evidence

Automated checks (CI-scoped paths per `.github/workflows/ci.yml`):

```
$ uv run ruff check hable_ya/ api/ tests/test_tool_handler.py tests/test_runner.py tests/test_health.py tests/test_prompts.py scripts/voice_client.py
All checks passed!

$ uv run mypy hable_ya/ api/ tests/test_tool_handler.py tests/test_runner.py tests/test_health.py tests/test_prompts.py
Success: no issues found in 36 source files

$ uv run pytest tests/
======================= 109 passed, 9 warnings in 4.32s ========================
```

Manual verification (reported by user, 2026-04-20):
- TTS with `es_ES-davefx-medium` sounds noticeably more natural than the prior `es_ES-carlfm-x_low` voice in a live Spanish session.
- STT with faster-whisper `medium` is better than `small` but still imperfect; accepted as the practical ceiling for this round.
- Piper voice downloaded on first use without manual intervention.
- No OOM when coexisting with Gemma on the dev GPU.
