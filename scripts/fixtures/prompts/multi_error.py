"""multi_error — 30 fixtures (6 per band)."""
from __future__ import annotations

from eval.fixtures.schema import CEFRBand

from . import ERROR_TYPES_BY_BAND

PRIORITY_RULE = """
Priority rule (must be applied consistently):
1. Errors that impede comprehension (highest)
2. High-frequency grammar (ser/estar, gender agreement, preterite)
3. Low-frequency grammar
4. Vocabulary gaps
5. Register mismatches (lowest)
"""


def build_user_prompts(n: int, band: CEFRBand) -> list[str]:
    allowed = ERROR_TYPES_BY_BAND[band]
    allowed_str = ", ".join(f"`{e}`" for e in allowed)
    prompts: list[str] = []
    for i in range(n):
        prompts.append(
            f"""Generate ONE `multi_error` fixture for CEFR band {band}.

The learner utterance must contain 2–3 distinct errors. The agent recasts
ONLY the highest-priority error and lets the others pass, but the `log_turn`
tool call records ALL errors observed.

{PRIORITY_RULE}

## Allowed error types for {band}

Use ONLY these exact strings for `errors_present`, `errors_observed`,
`priority_error`, and `ignored_errors`: {allowed_str}

Required fields in `expected`:
- `priority_error`: the error type that was recast
- `ignored_errors`: list of error types that were not recast

Metadata:
- `difficulty` must be "multi_error"
- `errors_present` lists all error types in the utterance

Required negative examples:
- `recasts_wrong_error` — recasts a lower-priority error
- `recasts_all_errors` — tries to fix every error
- `explicit_correction`
- `too_long`

Variation #{i + 1} of {n} — use different error combinations and themes."""
        )
    return prompts
