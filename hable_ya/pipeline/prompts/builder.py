"""Runtime system-prompt builder.

Thin wrapper over :func:`hable_ya.pipeline.prompts.render.render_system_prompt`
that constructs a neutral ``SystemParams`` from a minimal learner dict. Until
the learner model (#029) lands, the profile fields are static defaults — the
only knob that actually shapes output is the CEFR band.
"""
from __future__ import annotations

from eval.fixtures.schema import CEFRBand, LearnerProfile, SystemParams, Theme
from hable_ya.pipeline.prompts.register import COLD_START_INSTRUCTIONS
from hable_ya.pipeline.prompts.render import render_system_prompt

# Production-level midpoints per band so the rendered prompt's "L1 reliance"
# and "speech fluency" values aren't wildly off even though we don't track
# them live. Informational only — the band override is authoritative.
_BAND_MIDPOINT: dict[str, float] = {
    "A1": 0.1,
    "A2": 0.3,
    "B1": 0.5,
    "B2": 0.7,
    "C1": 0.9,
}

_NEUTRAL_THEME = Theme(
    domain="conversación abierta",
    prompt=(
        "Mantén una conversación natural con el estudiante. Deja que el "
        "estudiante elija el tema; si no propone uno, empieza con algo "
        "cotidiano (su día, familia, planes, algo que le guste)."
    ),
    target_structures=[],
)


def _neutral_profile(band: CEFRBand) -> LearnerProfile:
    level = _BAND_MIDPOINT.get(band, 0.5)
    return LearnerProfile(
        production_level=level,
        L1_reliance=0.5,
        speech_fluency=0.5,
        is_calibrated=False,
        sessions_completed=0,
        vocab_strengths=[],
        error_patterns=[],
    )


def build_system_prompt(learner: dict[str, object]) -> str:
    band_raw = learner.get("band", "A2")
    band: CEFRBand = band_raw if isinstance(band_raw, str) else "A2"  # type: ignore[assignment]
    params = SystemParams(profile=_neutral_profile(band), theme=_NEUTRAL_THEME)
    prompt = render_system_prompt(params, band=band)
    if learner.get("cold_start"):
        prompt = f"{prompt}\n\n## Primera sesión\n{COLD_START_INSTRUCTIONS}"
    return prompt
