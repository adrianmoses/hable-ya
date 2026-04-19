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
| 021 | WebSocket `/ws/session` voice session endpoint | planned | — |
| 022 | Pipecat pipeline composition (VAD → STT → prompt builder → LLM → tool handler → TTS) | planned | — |
| 023 | System prompt builder unified with `finetune/format.py` content | planned | — |
| 024 | REGISTER_BY_LEVEL + COLD_START_INSTRUCTIONS content | planned | — |
| 025 | Tool handler consuming `log_turn` calls from LLM output | planned | — |
| 026 | Turn observer persisting turn observations | planned | — |
| 027 | `HABLE_YA_TOOLS` schema definition (log_turn + any others) | planned | — |
| 028 | Postgres + Apache AGE: docker-compose service, async driver (asyncpg), schema, migrations, init script | planned | — |
| 029 | Learner profile module (state across sessions) | planned | — |
| 030 | Learner error-pattern tracking | planned | — |
| 031 | Learner vocabulary tracking | planned | — |
| 032 | Theme selection (`THEMES_BY_LEVEL` + `get_session_theme()`) | planned | — |
| 033 | Knowledge-graph learner model in Apache AGE (node/edge schema for skills, concepts, errors, progression) | planned | — |
| 034 | Agent eval: synthetic learner simulator with error patterns | planned | — |
| 035 | Agent eval: Opus session-outcome judge (pedagogical flow, level consistency, recast naturalness, learner production space, coherence) | planned | — |
| 036 | Agent eval orchestrator (end-to-end session runs) | planned | — |
| 037 | Interactive review TUI (fixtures + SFT datasets) | planned | — |
| 038 | Latency benchmark script | planned | — |
| 039 | Concurrency benchmark script (referenced in README, file absent) | planned | — |
| 040 | Session export script | planned | — |
| 041 | Database init script | planned | — |
| 042 | Model artifact / eval-result registry (link checkpoints to their datasets and scores) | planned | — |
| 043 | On-device deployment target (device class, OS, memory budget) | planned | — |
| 044 | Kaggle writeup and public share | planned | — |
| 045 | Multi-language support (source/target pairs beyond en→es) | planned | — |
| 046 | Web frontend for the voice agent (connects to `/ws/session`, mic capture + audio playback; scope TBD) | planned | — |

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
