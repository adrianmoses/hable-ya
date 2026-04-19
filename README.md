# hable-ya

An on-device, voice-first Spanish language-acquisition agent. A fine-tuned Gemma 4 E4B model acts simultaneously as the conversational partner, pedagogical assessor, and adaptive engine. The runtime is a Pipecat STT → LLM → TTS pipeline (faster-whisper + Gemma served via llama.cpp + piper-tts) exposed as a FastAPI WebSocket, with a knowledge-graph learner model (Postgres + Apache AGE) updated via tool calls.

Based on ideas from `comprende-ya` and `habla.practice`.

## Design docs

Product, architecture, and roadmap live under [`docs/specs/`](docs/specs/):

- [`OVERVIEW.md`](docs/specs/OVERVIEW.md) — product summary, target consumer, non-goals, tech stack
- [`ARCHITECTURE.md`](docs/specs/ARCHITECTURE.md) — component map, data flow, constraints
- [`ROADMAP.md`](docs/specs/ROADMAP.md) — feature list and status
- [`habla_fixture_spec.md`](habla_fixture_spec.md) — authoritative fixture specification

## Setup

Requires Python ≥3.12, `uv`, Docker with NVIDIA GPU support, and:

- `HF_TOKEN` — HuggingFace auth (Gemma is a gated model). Set env var or run `huggingface-cli login`.
- `ANTHROPIC_API_KEY` — only needed for fixture generation, recast judging, and agent eval.

```bash
uv sync --all-extras
cp .env.example .env   # then edit
```

## Usage

### Download the model

```bash
# Both GGUF (for llama.cpp) and HF weights (for fine-tuning)
python scripts/download_model.py

# GGUF only  → models/gemma-4-e4b.gguf
python scripts/download_model.py --gguf-only

# HF weights only  → models/gemma-4-e4b-hf/
python scripts/download_model.py --hf-only

# Re-download even if files exist
python scripts/download_model.py --force
```

### Serve the model

```bash
docker compose up llama
```

Serves the fine-tuned GGUF at `http://localhost:8080` (OpenAI-compatible).

### Generate eval fixtures

```bash
# Full pipeline: generate → validate → review → consolidate
python scripts/generate_eval_fixtures.py all

# Individual stages
python scripts/generate_eval_fixtures.py generate
python scripts/generate_eval_fixtures.py validate
python scripts/generate_eval_fixtures.py review
python scripts/generate_eval_fixtures.py consolidate
```

### Run model eval

Requires the llama.cpp server running.

```bash
python -m eval.run_eval --base-url http://localhost:8080 --output results.json

# Specific categories
python -m eval.run_eval --base-url http://localhost:8080 --output results.json \
    --categories single_error_recast,multi_error

# Concurrency and timeout
python -m eval.run_eval --base-url http://localhost:8080 --output results.json \
    --concurrency 8 --timeout 60
```

### Compare eval runs

```bash
python -m eval.compare baseline.json finetuned.json
```

Prints per-dimension and per-band deltas with threshold-based recommendations.

### Generate fine-tuning datasets

```bash
# Consolidate fixtures then generate the SFT dataset
python -m finetune.generate

# Skip consolidation if already done
python -m finetune.generate --no-consolidate

# Validate generated datasets
python -m finetune.validate
```

Outputs JSONL to `finetune/datasets/`. Training itself runs in [`notebooks/gemma4_finetune.ipynb`](notebooks/gemma4_finetune.ipynb) via Unsloth.

## Development

```bash
pytest
ruff check .
mypy .
```
