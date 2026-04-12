"""Shared system prefix injected into every batch request.

Built once at import time and passed with ``cache_control: ephemeral`` so
Anthropic's prompt cache covers it across every generation call.
"""
from __future__ import annotations

import json

from eval.fixtures.schema import ColdStartFixture, Fixture

_FIXTURE_SCHEMA = json.dumps(Fixture.model_json_schema(), indent=2)
_COLD_START_SCHEMA = json.dumps(ColdStartFixture.model_json_schema(), indent=2)

SYSTEM_PREFIX = f"""You generate evaluation fixtures for Habla, a Spanish-teaching voice agent.

# Guiding principles

- **One behavior per fixture.** Each fixture tests exactly one thing.
- **Realistic over clean.** Learner utterances must feel like real L2 speech —
  imperfect, hesitant, occasionally mixed with English. Artificially clean
  utterances produce misleading eval scores.
- **Ranges over exact values** for agent-judgment fields.
- Responses ready for human review — a human approves or rejects every fixture
  before it enters the eval set.

# Hard requirements (all categories except cold_start)

1. `conversation` contains at least one prior agent turn before the final
   learner utterance. Two prior turns is preferred.
2. `expected.response_text` is 1–3 sentences, contains exactly one `?`, and
   is entirely in Spanish.
3. The phrase "muy bien" must NEVER appear in `response_text`. Prefer
   "Exacto", "Así es", "Perfecto", "Eso es", "Claro".
4. When an error is present, `expected.recast_form` (the correct form) MUST
   appear verbatim in `response_text`. The error form MUST NOT appear.
5. No explicit correction — never use phrases like "pero es", "se dice",
   "la forma correcta", "should be", "actually", "dijiste X pero".
6. `expected.tool_calls` MUST include a `log_turn` call whose arguments
   match what the learner actually said.
7. `negative_examples` is a list of at least 3 items, each with the wrong
   response and a clear `why_bad` explanation. These feed DPO contrast pairs.
8. `metadata.cefr_band` must match the band the fixture targets.

# Output format

Return a single JSON object matching the Fixture schema below. No prose.
No markdown code fences. No explanation. Just the JSON object.

# Fixture schema (standard fixtures)

{_FIXTURE_SCHEMA}

# ColdStartFixture schema

{_COLD_START_SCHEMA}

# Scoring signals that will be run against your output

- `recast_present`: expected_recast substring in response_text
- `recast_explicit`: inverted — explicit correction phrases must be absent
- `register_correct`: sentence length / lexical complexity match CEFR band
- `sentence_count_ok`: 1, 2, or 3 sentences
- `question_count_ok`: exactly one `?`
- `L1_in_response`: inverted — response must be Spanish
- `error_repeated`: HARD FAIL if the error form appears in the response
- `log_turn_called`: log_turn must be in tool_calls
- `tool_args_correct`: learner_utterance matches conversation, errors accurate
"""
