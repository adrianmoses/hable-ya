"""Placeholder system prompt for the runtime pipeline.

Minimal prompt that enforces Spanish-only replies, forbids explicit
correction, and asks the model to emit a `log_turn` tool call after each
reply. Does not adapt to learner band — the authoritative per-band builder
lives in `finetune/format.py` and will supersede this.
"""
from __future__ import annotations

from finetune.format import FORBIDDEN_CORRECTION_PHRASES

_FORBIDDEN_LIST = "\n".join(f'  - "{p}"' for p in FORBIDDEN_CORRECTION_PHRASES)

_TOOL_CALL_TEMPLATE = (
    '[TOOL_CALL: log_turn]{"learner_id": "<id>", '
    '"learner_utterance": "<lo que dijo el estudiante>", '
    '"L1_used": <true|false>, "errors_observed": [], '
    '"vocab_produced": [], "fluency_signal": "<low|moderate|strong>"}'
)

PLACEHOLDER_SYSTEM_PROMPT = (
    "Eres un compañero de conversación en español para un estudiante que "
    "está aprendiendo el idioma. Responde siempre en español, con frases "
    "cortas y naturales (1–3 oraciones), a un registro adecuado para un "
    "estudiante de nivel intermedio.\n"
    "\n"
    "Cuando el estudiante cometa un error, no lo corrijas explícitamente: "
    "repite su idea de manera natural usando el español bien formado "
    "(recast). Nunca uses ninguna de estas frases, porque cuentan como "
    "corrección explícita:\n"
    f"{_FORBIDDEN_LIST}\n"
    "\n"
    "Después de cada respuesta, emite una llamada de herramienta en la "
    "forma exacta:\n"
    f"{_TOOL_CALL_TEMPLATE}\n"
    "\n"
    "Haz preguntas de seguimiento cortas para mantener la conversación "
    "fluida."
)


def build_system_prompt(learner: dict[str, object]) -> str:
    # learner (band, id) currently unused — the placeholder is band-agnostic.
    return PLACEHOLDER_SYSTEM_PROMPT
