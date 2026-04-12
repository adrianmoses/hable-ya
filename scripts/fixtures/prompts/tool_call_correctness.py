"""tool_call_correctness — 20 fixtures (4 per band)."""
from __future__ import annotations

from eval.fixtures.schema import CEFRBand


def build_user_prompts(n: int, band: CEFRBand) -> list[str]:
    prompts: list[str] = []
    for i in range(n):
        has_error = i % 2 == 0
        prompts.append(
            f"""Generate ONE `tool_call_correctness` fixture for CEFR band {band}.

Final learner turn: {"contains ONE error" if has_error else "is CLEAN (no errors)"}.

The primary signal under test is tool call accuracy. The conversational
response quality is secondary — an otherwise great response with inaccurate
`log_turn` arguments fails this category.

`log_turn.arguments` accuracy requirements:
- `learner_utterance` is a byte-exact match of the last user turn
- `errors_observed` is empty for clean turns, accurate for error turns
- `vocab_produced` only contains words the learner actually produced
- `fluency_signal` is a realistic assessment of the utterance
- `L1_used` accurately reflects whether English appeared

Required negative examples:
- `wrong_utterance_logged` — learner_utterance doesn't match
- `invented_errors` — logs errors that weren't there
- `wrong_vocab` — vocab_produced contains words not said
- `wrong_fluency_signal` — obviously miscalibrated signal
- `missing_log_turn` — no log_turn tool call at all

Variation #{i + 1} of {n}."""
        )
    return prompts
