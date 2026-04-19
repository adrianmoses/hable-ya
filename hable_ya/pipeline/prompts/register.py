"""Per-band register guidance and cold-start posture for the runtime prompt.

The register map is the single source of truth in
:mod:`hable_ya.pipeline.prompts.render`; this module re-exports it under the
``REGISTER_BY_LEVEL`` alias that callers already use. ``COLD_START_INSTRUCTIONS``
is a short Spanish guidance string used when the learner is on session 1 (no
real diagnostic flow in this slice — see spec 023).
"""
from __future__ import annotations

from hable_ya.pipeline.prompts.render import REGISTER_GUIDANCE

REGISTER_BY_LEVEL: dict[str, str] = dict(REGISTER_GUIDANCE)

COLD_START_INSTRUCTIONS: str = (
    "Esta es la primera conversación con el estudiante. Empieza con un "
    "saludo cálido y natural, y propón un tema cotidiano con una pregunta "
    "abierta (por ejemplo: familia, la semana del estudiante, algo que le "
    "gusta hacer). No preguntes por el nivel del estudiante, no anuncies "
    "que es la primera sesión, y no pidas permiso para empezar — "
    "simplemente empieza la conversación."
)
