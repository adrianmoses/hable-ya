"""recast_present and recast_explicit checks."""
from __future__ import annotations

import re


_EXPLICIT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"no se dice",
        r"la forma correcta",
        r"debes decir",
        r"deberías decir",
        r"se dice",
        r"la palabra correcta",
        r"en realidad es",
        r"quieres decir",
        r"quisiste decir",
        r"correcto es",
        r"\berror\b",
    ]
]


def recast_present(response: str, recast_form: str | None) -> bool:
    """Check whether the expected recast form appears in the model response."""
    if recast_form is None:
        return True
    return recast_form.lower() in response.lower()


def recast_explicit(response: str) -> bool:
    """Return True if the response contains explicit correction language.

    A return value of True is *bad* — the model is correcting explicitly
    rather than recasting naturally.
    """
    return any(p.search(response) for p in _EXPLICIT_PATTERNS)


def error_repeated(response: str, error_forms: list[str]) -> bool:
    """Return True if the model reproduces any of the learner's error forms.

    A return value of True is a hard failure — the agent should never
    reproduce the learner's mistake.
    """
    lower = response.lower()
    return any(form.lower() in lower for form in error_forms if form)
