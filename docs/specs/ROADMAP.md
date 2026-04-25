# Roadmap

<!-- status: inferred -->
| Field | Value |
|---|---|
| status | planned |
| created | 2026-04-19 |

## Features

| ID | Feature | Status | Spec |
|---|---|---|---|
| 001 | Fixture schema (standard + cold_start) | implemented | — |
| 002 | Per-category fixture generation via Anthropic Batches API (8 categories × 5 bands) | implemented | habla_fixture_spec.md |
| 003 | Fixture pre-review validation (leak detection, shape checks) | implemented | — |
| 004 | Fixture review workflow (manual file moves between _pending/_approved/_rejected) | in-progress | — |
| 005 | Fixture consolidation into canonical per-category JSON | implemented | — |
| 006 | Model eval runner against llama.cpp OpenAI-compatible endpoint | implemented | — |
| 007 | Tool-call parser for 3 surface formats (OpenAI struct, `[TOOL_CALL: name]{...}`, `name({...})`) | implemented | — |
| 008 | 7-dimension pedagogical + tool-fidelity scoring | implemented | — |
| 009 | spaCy-based recast heuristic (Spanish lemma + stem fallback) | implemented | — |
| 010 | CEFR register heuristic | implemented | — |
| 011 | English-in-response detection (langdetect with short-text filter) | implemented | — |
| 012 | Opus second-pass recast judge with disk cache | implemented | — |
| 013 | Baseline-vs-finetuned eval comparator with threshold-driven recommendations | implemented | — |
| 014 | SFT JSONL dataset generator (3 of 8 categories: recast + multi_error + tool_call_correctness) | implemented | — |
| 015 | SFT dataset validator (tool-call parse, metadata tallies, strict mode) | implemented | — |
| 016 | Unsloth SFT fine-tune in notebook | implemented | — |
| 017 | Gemma 4 E4B download (GGUF + HF weights) from HuggingFace | implemented | — |
| 018 | llama.cpp CUDA Docker Compose serving of fine-tuned GGUF | implemented | — |
| 019 | FastAPI app skeleton with /health endpoint | implemented | — |
| 020 | Pytest suite for scoring heuristics | implemented | — |
| 021 | WebSocket `/ws/session` voice session endpoint | implemented | [021-voice-pipeline](021-voice-pipeline/spec.md) ([decision](021-voice-pipeline/decision.md)) |
| 022 | Pipecat pipeline composition (VAD → STT → prompt builder → LLM → tool handler → TTS) | implemented | [021-voice-pipeline](021-voice-pipeline/spec.md) ([decision](021-voice-pipeline/decision.md)) |
| 023 | System prompt builder unified with `finetune/format.py` content | implemented | [023-agent-loop](023-agent-loop/spec.md) ([decision](023-agent-loop/decision.md)) |
| 024 | REGISTER_BY_LEVEL + COLD_START_INSTRUCTIONS content | implemented | [023-agent-loop](023-agent-loop/spec.md) ([decision](023-agent-loop/decision.md)) |
| 025 | Tool handler consuming `log_turn` calls from LLM output | implemented | [023-agent-loop](023-agent-loop/spec.md) ([decision](023-agent-loop/decision.md)) |
| 026 | Turn observer persisting turn observations | planned | — |
| 027 | `HABLE_YA_TOOLS` schema definition (log_turn + any others) | implemented | [023-agent-loop](023-agent-loop/spec.md) ([decision](023-agent-loop/decision.md)) |
| 028 | Postgres + Apache AGE: docker-compose service, async driver (asyncpg), schema, migrations, init script | implemented | [028-postgres-age-setup](028-postgres-age-setup/spec.md) ([decision](028-postgres-age-setup/decision.md)) |
| 029 | Learner profile module (state across sessions) | in-progress | [029-learner-model](029-learner-model/spec.md) |
| 030 | Learner error-pattern tracking | in-progress | [029-learner-model](029-learner-model/spec.md) |
| 031 | Learner vocabulary tracking | in-progress | [029-learner-model](029-learner-model/spec.md) |
| 032 | Theme selection (`THEMES_BY_LEVEL` + `get_session_theme()`) | in-progress | [029-learner-model](029-learner-model/spec.md) |
| 033 | Knowledge-graph learner model in Apache AGE (node/edge schema for skills, concepts, errors, progression) | in-progress | [029-learner-model](029-learner-model/spec.md) |
| 034 | Agent eval: synthetic learner simulator with error patterns | implemented | [034-agent-eval](034-agent-eval/spec.md) ([decision](034-agent-eval/decision.md)) |
| 035 | Agent eval: Opus session-outcome judge (pedagogical flow, level consistency, recast naturalness, learner production space, coherence) | implemented | [034-agent-eval](034-agent-eval/spec.md) ([decision](034-agent-eval/decision.md)) |
| 036 | Agent eval orchestrator (end-to-end session runs) | implemented | [034-agent-eval](034-agent-eval/spec.md) ([decision](034-agent-eval/decision.md)) |
| 037 | Interactive review TUI (fixtures + SFT datasets) | planned | — |
| 038 | Latency benchmark script | planned | — |
| 039 | Concurrency benchmark script (referenced in README, file absent) | planned | — |
| 040 | Session export script | planned | — |
| 041 | Database init script | implemented | [028-postgres-age-setup](028-postgres-age-setup/spec.md) ([decision](028-postgres-age-setup/decision.md)) |
| 042 | Model artifact / eval-result registry (link checkpoints to their datasets and scores) | planned | — |
| 043 | On-device deployment target (device class, OS, memory budget) | planned | — |
| 044 | Kaggle writeup and public share | planned | — |
| 045 | Multi-language support (source/target pairs beyond en→es) | planned | — |
| 046 | Web frontend for the voice agent (connects to `/ws/session`, mic capture + audio playback; scope TBD) | implemented | [046-web-frontend-voice-shell](046-web-frontend-voice-shell/spec.md) |
| 047 | STT quality investigation (Whisper model size, VAD tuning, `initial_prompt` priming) — closed at medium-Whisper quality ceiling | implemented | [048-stt-tts-quality-and-latency](048-stt-tts-quality-and-latency/spec.md) ([decision](048-stt-tts-quality-and-latency/decision.md)) |
| 048 | STT → faster-whisper `medium`, TTS → `es_ES-davefx-medium`, debug-only end-to-end latency via Pipecat `UserBotLatencyObserver` | implemented | [048-stt-tts-quality-and-latency](048-stt-tts-quality-and-latency/spec.md) ([decision](048-stt-tts-quality-and-latency/decision.md)) |
| 049 | Auto-updating learner level from error/vocabulary signals (promotion/demotion driven by aggregate trends) | planned | — |
| 050 | Initial-level placement / calibration (derive starting CEFR level from first N sessions instead of config) | planned | — |

## Status Values

- `planned` — not yet started
- `in-progress` — spec written, implementation underway
- `implemented` — decision record complete
- `deprecated` — removed from product

## Revision History

| Date | Change |
|---|---|
| 2026-04-19 | Initial roadmap inferred by audit skill |
| 2026-04-19 | Decisions recorded: single-tenant only; persistence = PostgreSQL + Apache AGE (replaces planned SQLite) |
| 2026-04-19 | Added #046 web frontend for the voice agent (scope TBD) |
| 2026-04-19 | Spec 021-voice-pipeline drafted; #021 + #022 → in-progress |
| 2026-04-19 | Spec 021-voice-pipeline implemented; #021 + #022 → implemented. Package renamed `hable-ya/` → `hable_ya/`. |
| 2026-04-19 | Spec 046-web-frontend-voice-shell drafted (scoped to Home + Session, orb variant A, no captions); #046 → in-progress. |
| 2026-04-19 | Spec 046-web-frontend-voice-shell implemented; #046 → implemented. Backend change: added `hable_ya/pipeline/serializer.py::RawPCMSerializer` (wired into `api/routes/session.py`); Pipecat JS SDK dropped in favor of native WebSocket + AudioWorklet. |
| 2026-04-19 | Spec 023-agent-loop drafted (bundles #023 + #024 + #025 + #027: unified per-band system prompt, REGISTER_BY_LEVEL + COLD_START content, `log_turn` tool handler with JSONL sink, HABLE_YA_TOOLS schema); #026 (persistence) deferred. |
| 2026-04-19 | Spec 023-agent-loop implemented; #023 + #024 + #025 + #027 → implemented. Unified prompt renderer in `hable_ya/pipeline/prompts/render.py` (shared with `finetune/format.py`); `log_turn` parsed to JSONL sink + ring buffer + `GET /dev/observations`; disabled Gemma thinking mode via `chat_template_kwargs.enable_thinking=false`. |
| 2026-04-19 | Added #047 STT quality investigation (current Whisper `small` produces poor Spanish transcripts under live conditions). |
| 2026-04-20 | Spec 048-stt-tts-quality-and-latency drafted (bundles #047 resolution + #048: Whisper `medium`, Piper medium voice, debug-only end-to-end latency probe); #047 + #048 → in-progress. |
| 2026-04-20 | Spec 048-stt-tts-quality-and-latency implemented; #047 + #048 → implemented. Reused Pipecat's built-in `UserBotLatencyObserver` instead of a custom FrameProcessor (observers sit outside the frame chain). STT quality accepted at the medium-Whisper ceiling. |
| 2026-04-21 | Spec 028-postgres-age-setup drafted (bundles #028 + #041: `apache/age:release_PG18_1.7.0` compose service, asyncpg runtime pool with AGE bootstrap, alembic async migrations with raw-SQL `op.execute` revisions, `scripts/init_db.py`; plumbing only — no learner schema); #028 + #041 → in-progress. Learner-model schema deferred to its own spec (#029–#033). |
| 2026-04-21 | Spec 028-postgres-age-setup implemented; #028 + #041 → implemented. Divergences from spec (captured in the decision record): host port remapped `5432 → 5433` (system-Postgres collision), PG18 volume at `/var/lib/postgresql` (not `/data`), `ALTER ROLE` in migration so `search_path` survives asyncpg's `RESET ALL`, pytest-asyncio session loop scope set globally, `::name` cast required for `drop_graph`. `Settings.async_database_url` property centralises the `postgresql+asyncpg://` rewrite. |
| 2026-04-22 | Learner-model bundle (#029–#033) scoped on branch `spec-learner-model-029-033`. Added #049 (auto-updating level) and #050 (initial-level placement) as deferred follow-ups — the 029–033 spec will keep level static/manual and hand-author scenario content inline. |
| 2026-04-22 | Spec 029-learner-model drafted (bundles #029 + #030 + #031 + #032 + #033: hybrid relational + AGE graph storage, `log_turn` ingestion path, profile-driven prompt rendering, 50 hand-authored per-band scenarios with 3-session cooldown). Static band (no auto-promotion), one `:Learner` node, AGE graph writes in same transaction as aggregate updates; #029–#033 → in-progress. |
| 2026-04-24 | Spec 034-agent-eval drafted on branch `spec-agent-eval` (bundles #034 + #035 + #036: Opus-driven synthetic learner with disk-cached utterances, 5-dimension Opus session judge with disk-cached verdicts, orchestrator emitting results parallel to `eval/run_eval.py`). In-memory profile accumulator shares `compute_snapshot` with `LearnerProfileRepo` via a new `hable_ya/learner/aggregations.py`; talks to llama.cpp directly (no Pipecat/WS); #034–#036 → in-progress. |
| 2026-04-25 | Spec 034-agent-eval implemented; #034 + #035 + #036 → implemented. Divergences from spec (captured in the decision record): `SessionVerdict.rationale` upgraded from `dict[str, str]` to a structured `Rationale` model with 5 required fields (`JUDGE_SYSTEM_VERSION` bumped to "2") after first smoke showed Opus returning `{}`; `compute_snapshot` takes pre-tallied inputs (Counter + last-seen maps) instead of `list[turns]` so the SQL-backed repo doesn't have to denormalize; cost-preview narrowed to honest worst-case (only first-learner-turn cache state is predictable); `--dry-run` resolves personas only, doesn't render full prompts. Spec gaps surfaced: llama.cpp at `temperature=0.0` is not fully deterministic (subsequent runs cost ~$0.50–$2 not $0); `log_turn` emission was 100% on smoke (vs ~80% memory). |
