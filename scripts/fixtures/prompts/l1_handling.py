"""l1_handling — 25 fixtures (5 per band)."""
from __future__ import annotations

from eval.fixtures.schema import CEFRBand

SCENARIOS = [
    ("full_english", "Learner responds entirely in English."),
    ("mixed_switch", "Learner starts in Spanish then switches mid-sentence."),
    ("english_word", "Learner uses one English word when vocabulary fails."),
    ("meta_question", "Learner asks 'how do you say X?' in English."),
    ("full_english", "Learner responds entirely in English, second variation."),
]


def build_user_prompts(n: int, band: CEFRBand) -> list[str]:
    prompts: list[str] = []
    for i in range(n):
        label, desc = SCENARIOS[i % len(SCENARIOS)]
        prompts.append(
            f"""Generate ONE `l1_handling` fixture for CEFR band {band}.

L1 scenario: **{label}** — {desc}

Agent rules:
- Response MUST be 100% Spanish, no English tokens anywhere
- Provide enough context/scaffolding so the learner can infer meaning
  without a translation
- For `meta_question`: answer by embedding the target word naturally in a
  Spanish sentence, not by giving "word = palabra" translations
- `metadata.L1_used` must be true
- `log_turn.arguments.L1_used` must be true
- Tag the fixture with the scenario label

Required negative examples:
- `switches_to_english` — agent uses English
- `ignores_question` — doesn't answer the meta question
- `over_explains` — 4+ sentences explaining
- `register_mismatch`

Variation #{i + 1} of {n}."""
        )
    return prompts
