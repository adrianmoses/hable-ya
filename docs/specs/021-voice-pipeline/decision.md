# Decision Record: Voice Agent Pipeline (WebSocket + Pipecat + faster-whisper + piper-tts)

| Field | Value |
|---|---|
| id | 021 |
| status | implemented |
| created | 2026-04-19 |
| spec | [spec.md](./spec.md) |

---

## Context

The spec was unusually detailed — it had already been aligned with a working reference implementation (`../comprende-ya-old/voice-agent/voice_agent_pipecat.py`) running the same class of pipeline against the same `pipecat-ai>=0.0.103` line, so most topology and API decisions were already locked in. Going in, the open work was: instantiate the stubs, wire Gemma 4 E4B's training-time tool-call convention into a stripping processor, and find a testing posture that gives real coverage without live audio.

Three things shaped the implementation beyond the spec:

1. **Latent package-layout bug.** The repo's runtime package lived at `hable-ya/` (hyphen). Python cannot import a hyphenated module name, and no code imported from it yet, so the breakage was invisible. The first `from hable_ya...` import would have failed. The rename was a prerequisite, not a feature.

2. **CUDA bootstrap interacts badly with pytest.** The reference's `_ensure_nvidia_lib_path()` re-execs the Python process to get `LD_LIBRARY_PATH` right for cuBLAS/cuDNN. Useful for the server; destructive during test collection — every `from api.main import app` in a fixture triggered a re-exec loop that either hung the runner or produced doubled `pytest` startup banners. Needed a test-mode guard.

3. **User constraint: no live audio.** No mic, no Whisper on real hardware, no Piper producing audible output in CI. Shaped the test strategy significantly — the only truly end-to-end validation is a human-run `scripts/voice_client.py` against a dev server; everything else tests pieces.

---

## Decision

Built the pipeline per the spec's topology: `transport.input → stt → turn_observer → aggregators.user → llm → tool_handler → tts → transport.output → aggregators.assistant`. Services (`WhisperSTTService` CUDA float16, `OpenAILLMService` against llama.cpp, `PiperTTSService` with `es_ES-carlfm-x_low`) load once in a FastAPI `lifespan` and are shared across WS connections. Per-connection state (transport, `LLMContext`, Smart Turn V3 + Silero VAD analyzers, two custom processors) is built fresh inside the WS handler.

`HableYaToolHandler` buffers every `LLMTextFrame` between `LLMFullResponseStartFrame` and `LLMFullResponseEndFrame`, strips `[TOOL_CALL: ...]{...}` and `name({...})` spans at end-of-response using a new shared helper `strip_tool_calls()` in `eval/scoring/turn.py`, and emits one cleaned `LLMTextFrame` downstream. This places the stripper strictly before TTS so tool syntax never reaches speech synthesis (deliberate divergence from the reference's interceptor-after-TTS placement).

`/health` returns 503 during warmup and 200 after the llama.cpp warmup ping succeeds; the WS endpoint refuses with close-code 1013 while `app.state.ready is False`. `PLACEHOLDER_SYSTEM_PROMPT` is a ~15-line Spanish prompt flagged for replacement in #023; it shares `FORBIDDEN_CORRECTION_PHRASES` with `finetune/format.py` so a future change to the forbidden set updates both places at once.

---

## Alternatives Considered

### Tool-handler streaming strategy

**Option A — Buffer the full response, strip at `LLMFullResponseEndFrame`.**
- Pros: single input → single output (trivially testable); reuses the eval-side regex/parser exactly; robust against tool-call-marker tokens split across frames; lands a shape #025 wants anyway (real dispatch needs the full parsed payload).
- Cons: nominal concern that buffering adds latency.

**Option B — Streaming with a hold-back window.**
- Pros: no added latency in theory.
- Cons: needs a partial-match state machine that the shared parser doesn't support, so new regex surface; classic off-by-one territory when simulating chunk boundaries; #025 would throw it away when dispatch lands.

**Option C — Hybrid (stream until a suspicious prefix appears, then buffer).**
- Pros: best of both in principle.
- Cons: combines the complexity of B with a new decision (what counts as a "suspicious prefix"); highest-complexity option.

**Chosen: Option A.** The latency argument against it turned out not to hold — Piper TTS synthesizes at sentence boundaries, not per token, so the first-TTS-frame time is governed by the first sentence finishing, not by buffering the tail. Training data guarantees `[TOOL_CALL: ...]` is always at the tail of a response, so there is no in-response speech that a streaming strategy would rescue. Making tests trivial mattered given the no-live-audio constraint.

### Package rename vs. keep hyphenated directory

**Option A — Rename `hable-ya/` → `hable_ya/`.**
- Pros: Python imports work; `uv sync` editable install resolves cleanly.
- Cons: touches 22 files via `git mv`; changes every future import path.

**Option B — Keep the hyphenated name and use `__init__.py` tricks.**
- Pros: no rename.
- Cons: no standard supports this — hyphens aren't valid Python identifiers. Would require a meta-path hook, which is exotic and breaks tooling (mypy, ruff, IDEs).

**Chosen: Option A.** Option B isn't really an option; Python doesn't permit it.

### CUDA bootstrap guard under pytest

**Option A — Skip the re-exec when `PYTEST_CURRENT_TEST` env var is set or `sys.argv[0]` contains "pytest".**
- Pros: minimal code change; no test setup required; tests don't need the CUDA libs on `LD_LIBRARY_PATH` anyway (Whisper is mocked).
- Cons: silent behavior change in a bootstrap module — someone writing a test that actually wants CUDA libs would be surprised.

**Option B — Remove the re-exec from import time and require the server to be started via a wrapper script that sets `LD_LIBRARY_PATH` first.**
- Pros: no test-specific logic; bootstrap becomes a no-op.
- Cons: adds a new failure mode for operators who forget the wrapper; reference implementation's approach is proven and pleasant.

**Option C — Gate on an explicit env var like `HABLE_YA_SKIP_CUDA_BOOTSTRAP=1`, set by `conftest.py`.**
- Pros: intent is explicit.
- Cons: strictly more setup than A; easy to forget in a new test file.

**Chosen: Option A.** Lowest-friction path. The silent-skip concern is limited to tests — production startup is unaffected.

### Tool-call span stripping — new helper vs. inline regex

**Option A — Add `find_tool_call_spans` + `strip_tool_calls` to `eval/scoring/turn.py`.**
- Pros: one regex definition covers eval scoring, runtime stripping, and the future #025 dispatcher; runtime and offline scoring can never drift.
- Cons: grows the `eval/scoring/turn.py` surface area by two public functions.

**Option B — Inline the regex in `tool_handler.py`.**
- Pros: keeps eval module unchanged.
- Cons: duplicated regex that must be kept in sync with `parse_tool_calls`'s `_TOOL_CALL_HEADER_RE` / `_FUNCTION_CALL_RE` by hand.

**Chosen: Option A.** The existing parser already had the closing-brace / JSON-payload matching logic (`_parse_args_payload`) that stripping needs. Consolidating there avoids a latent bug where the eval scorer and runtime stripper disagree about what counts as a tool call.

### Promote `_FORBIDDEN_CORRECTION_PHRASES` to public

**Option A — Rename to `FORBIDDEN_CORRECTION_PHRASES` and import from both `finetune/format.py` and the new prompt builder + test.**
- Pros: single source of truth; explicit public API.
- Cons: crosses a light private/public convention line in `finetune/format.py`.

**Option B — Import the private name from another module.**
- Pros: no rename.
- Cons: importing `_FORBIDDEN_CORRECTION_PHRASES` across module boundaries is a smell; accidentally signals this is an unstable name.

**Chosen: Option A.** Three consumers (the SFT formatter, the runtime prompt builder, and two tests) is past the threshold where the name should be public.

### Test coverage without live audio — synthetic frame injection vs. mocked services

**Option A — Mock `WhisperSTTService` in runner tests, test only composition + observable behavior in everything else.**
- Pros: all four test files pass deterministically, no model downloads; catches the regression this spec actually risks (processor misordering, warmup-state leak).
- Cons: doesn't exercise the real frame-flow through a live pipeline.

**Option B — Full synthetic pipeline with fake STT/LLM/TTS replacing the real services, driven by `queue_frames()`.**
- Pros: closer to end-to-end.
- Cons: tests the fakes as much as the code; high maintenance for low additional signal given the real pipecat machinery (aggregators, VAD) wasn't on the risk list.

**Chosen: Option A.** Plan agent's guidance matched this bias and was persuasive. The one end-to-end path that matters — audio in, audio out — is `scripts/voice_client.py` run by a human against a live server.

---

## Tradeoffs

What the chosen approach optimises for:

- **Shippability of a testable foundation.** The feature is done in one pass with a test suite that runs in 3.5s on any dev box. Subsequent runtime specs (#025–#029) can land without any of them needing to re-learn the pipecat API.
- **Consolidation around `eval/scoring/turn.py`.** Every place that interprets `[TOOL_CALL: ...]{...}` now goes through the same regex. #025's tool dispatcher inherits this for free.
- **Explicit scope boundaries.** The `DEFAULT_LEARNER` constant and `PLACEHOLDER_SYSTEM_PROMPT` are both flagged in code for the specs that will replace them; nothing creeps into this PR that belongs in #023/#025/#026/#029.

What was given up:

- **No automated end-to-end proof that audio actually flows.** Whisper-in-CUDA, Piper synthesis, Smart Turn V3 behavior under L2 Spanish pauses, VRAM co-residency with Gemma 4 E4B Q8_0 — all require human-run validation via `scripts/voice_client.py`. The acceptance criteria in the spec that rely on measured latency (p50 ≤ 1.5s) and on actual multi-turn sessions can only be checked manually.
- **No CI.** The test suite is run locally only; nothing yet gates PRs on it.
- **Non-functional `aiosqlite`-era `db_path` config field.** Left in place because `hable_ya/db/` has migration scaffolding that will be repurposed for Postgres + AGE in #028. The dead field is strictly cheaper to carry than to temporarily remove and reintroduce.
- **CUDA bootstrap skips in tests by argv / env heuristic.** A principled fix (explicit env var, or removing the re-exec entirely) is cleaner but wasn't in scope.

---

### Spec Divergence

The build matched the spec in structure and intent. Divergences are all small and mostly mechanical — API shapes that differ in the installed pipecat version and a handful of things discovered during implementation.

| Spec Said | What Was Built | Reason |
|---|---|---|
| (implicit) package is importable as `hable_ya` | Renamed `hable-ya/` → `hable_ya/` + updated `pyproject.toml` `packages` list | Hyphenated Python package names aren't importable. Latent bug no one had triggered because nothing imported from the runtime package until this spec. |
| `SileroVADAnalyzer(sample_rate=..., stop_secs=...)` | `SileroVADAnalyzer(sample_rate=..., params=VADParams(stop_secs=...))` | Pipecat 0.0.108's constructor takes `params=VADParams(...)`, not a `stop_secs` kwarg directly. |
| Reuse `eval/scoring/turn.py:parse_tool_calls()` for stripping | Added new `find_tool_call_spans()` + `strip_tool_calls()` in the same module | `parse_tool_calls` returns parsed args but not the byte spans needed to excise text. The new helpers sit next to it and share the same regexes. |
| `finetune.format._FORBIDDEN_CORRECTION_PHRASES` imported into the prompt builder | Renamed the constant to `FORBIDDEN_CORRECTION_PHRASES` (public) before importing | Three consumers (SFT formatter, prompt builder, test) — past the threshold where a private name is appropriate. |
| Placeholder prompt text: "integra la forma correcta de manera natural" | Reworded to "repite su idea de manera natural usando el español bien formado" | First draft seeded the model with the forbidden phrase "la forma correcta" even in meta-instructions. A test caught it; the reword preserves meaning while keeping forbidden vocabulary out of the prompt entirely. |
| `cuda_bootstrap` imported first in `api/main.py` | Same, plus pytest-mode guard that skips the re-exec when running under pytest | Without the guard, every test that imports `api.main` via a fixture triggered a process re-exec, thrashing the test runner. Guard keys on `PYTEST_CURRENT_TEST` env var or `"pytest" in sys.argv[0]`. |
| `audio_sample_rate: int = 16000` implied across many config fields | Added as a single config field `settings.audio_sample_rate` | Spec listed it inline for several services but not as a top-level knob. Promoting it avoids three places needing to be updated together. |
| WS-refused-during-warmup not explicitly specified beyond "connects and closes" | Refuses with close-code 1013 ("try again later") and never calls `accept()` | Test evidence needed a deterministic refusal signal; 1013 is the standard code for "service overloaded / warming up". |

---

## Spec Gaps Exposed

- **Spec did not catch the package rename prerequisite.** Any future runtime spec that refers to `hable_ya` imports is fine now, but the OVERVIEW / ARCHITECTURE docs still refer to the old `hable-ya/` path in places. Worth an audit pass.
- **Spec's acceptance criteria include manual-only measurements** (p50 first-token latency, 4-way concurrency, Smart Turn pause behavior, VRAM co-residency). These are good criteria, but the decision record is their only home until a latency-benchmark script lands (#038). Worth adding a "manual verification checklist" section to the spec template for future specs so unchecked boxes don't accidentally age into an "unknown" state.
- **Spec underestimated the CUDA-libs interaction with pytest.** The validation checklist focused on live-run concerns; the test-time re-exec interaction showed up only during implementation. Worth noting in the reference to `cuda_bootstrap` going forward.
- **Placeholder prompt wording is a minor pedagogical risk vector.** Even meta-instructions in the system prompt can seed vocabulary. The test I added for this is feature-specific; the pattern (treat forbidden phrases as forbidden even in instructions about them) belongs in the #023 spec as an explicit rule.
- **`audio_sample_rate` and `llm_model_name` were implicit in the spec.** Both needed to be promoted to config fields. Small, but a reminder that every mentioned value is a potential config knob.

---

## Test Evidence

**`pytest tests/test_prompts.py tests/test_tool_handler.py tests/test_runner.py tests/test_health.py -v`**

```
tests/test_prompts.py::test_prompt_is_non_empty_spanish PASSED           [  6%]
tests/test_prompts.py::test_prompt_asks_for_tool_call PASSED             [ 12%]
tests/test_prompts.py::test_prompt_does_not_use_forbidden_phrases_in_instructions PASSED [ 18%]
tests/test_prompts.py::test_placeholder_constant_matches_builder_output PASSED [ 25%]
tests/test_tool_handler.py::test_strips_tool_call_at_tail PASSED         [ 31%]
tests/test_tool_handler.py::test_passes_through_when_no_tool_call PASSED [ 37%]
tests/test_tool_handler.py::test_strips_function_call_style PASSED       [ 43%]
tests/test_tool_handler.py::test_malformed_tool_call_passes_through PASSED [ 50%]
tests/test_tool_handler.py::test_non_llm_frames_pass_through_unchanged PASSED [ 56%]
tests/test_tool_handler.py::test_empty_response_does_not_emit_text_frame PASSED [ 62%]
tests/test_runner.py::test_build_pipeline_returns_pipeline PASSED        [ 68%]
tests/test_runner.py::test_pipeline_processor_order PASSED               [ 75%]
tests/test_runner.py::test_custom_processors_are_fresh_per_pipeline PASSED [ 81%]
tests/test_health.py::test_health_returns_200_after_warmup PASSED        [ 87%]
tests/test_health.py::test_health_returns_503_before_warmup_completes PASSED [ 93%]
tests/test_health.py::test_ws_session_refused_while_warming_up PASSED    [100%]
```

**Full repo suite — `pytest tests/` — 78 passed, 9 warnings in 3.49s** (62 pre-existing tests still green after the package rename + the new helpers in `eval/scoring/turn.py`).

**`mypy hable_ya/ api/`** — `Success: no issues found in 27 source files` (strict mode per `pyproject.toml`).

**`ruff check hable_ya/ api/ tests/test_tool_handler.py tests/test_runner.py tests/test_health.py tests/test_prompts.py scripts/voice_client.py`** — `All checks passed!` (pre-existing repo-wide lint issues in other modules are out of scope for this spec).

### Manual verification — deferred

Per spec's §Testing Approach, the following are human-run steps that cannot be automated in the no-live-audio environment:

- [ ] `docker compose up llama` + `uvicorn api.main:app`; confirm `/health` 503 → 200 over ~30s startup.
- [ ] `python scripts/voice_client.py <in.wav> <out.wav>` against the live server; confirm `out.wav` is non-silent Spanish speech.
- [ ] `nvidia-smi` during a session: record peak VRAM with Gemma 4 E4B Q8_0 and Whisper-small float16 co-resident.
- [ ] Four parallel `voice_client.py` runs complete without errors.
- [ ] p50 / p95 first-TTS-frame latency recorded from `PipelineTask(enable_metrics=True)` log lines.
- [ ] Smart Turn V3 subjective pause test: 2 s mid-sentence pause should not end the turn; 4 s tail pause should.

These ride with whoever next runs the server against a live GPU. Until then, the spec's acceptance criteria that depend on them are "demonstrated by code, not by measurement."
