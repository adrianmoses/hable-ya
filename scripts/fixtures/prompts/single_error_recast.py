"""single_error_recast — 50 fixtures (10 per band)."""
from __future__ import annotations

from eval.fixtures.schema import CEFRBand

from . import ERROR_TYPES_BY_BAND

DOMAINS = [
    "food_and_cooking", "family_and_friends", "work_and_study",
    "travel_and_transport", "weekend_plans", "hobbies_and_music",
    "health_and_sports", "city_life", "weather_and_seasons", "shopping",
]


def build_user_prompts(n: int, band: CEFRBand) -> list[str]:
    errors = ERROR_TYPES_BY_BAND[band]
    prompts: list[str] = []
    for i in range(n):
        error = errors[i % len(errors)]
        domain = DOMAINS[i % len(DOMAINS)]
        prompts.append(
            f"""Generate ONE `single_error_recast` fixture for CEFR band {band}.

Error type to showcase: **{error}**
Theme domain: **{domain}**

The learner utterance must contain exactly one natural-sounding instance of
this error — nothing else wrong. The agent response must:
- Recast the error implicitly (correct form appears, error form absent)
- Be in the register appropriate for {band}
- Be 1–3 sentences with exactly one question
- Include a `log_turn` tool call capturing the error accurately

Provide these four negative examples (required labels):
- `explicit_correction` — names the error directly
- `no_recast` — correct form never appears
- `too_long` — 4+ sentences, agent dominates
- `register_mismatch` — register far above/below {band}

Set `metadata.difficulty` to "straightforward" or "ambiguous" based on how
obvious the error is."""
        )
    return prompts
