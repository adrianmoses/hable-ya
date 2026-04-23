"""Tool schemas advertised to the LLM.

Runtime pipeline defines one tool today: ``log_turn``. Its argument shape is
the same canonical 4-key payload used throughout fixture eval, SFT training,
and the runtime observation sink (see ``hable_ya.pipeline.prompts.render``
for the shared constants).
"""

from __future__ import annotations

LOG_TURN_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "log_turn",
        "description": (
            "Record a structured observation of the learner's last turn. Call "
            "exactly once after every reply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "learner_utterance": {
                    "type": "string",
                    "description": "The learner's last message copied verbatim.",
                },
                "errors": {
                    "type": "array",
                    "description": (
                        "Errors observed in the learner's last turn. Empty "
                        "list if none."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "produced": {"type": "string"},
                            "target": {"type": "string"},
                        },
                        "required": ["type", "produced", "target"],
                        "additionalProperties": False,
                    },
                },
                "fluency_signal": {
                    "type": "string",
                    "enum": ["weak", "moderate", "strong"],
                    "description": ("Overall fluency read of the learner's last turn."),
                },
                "L1_used": {
                    "type": "boolean",
                    "description": (
                        "True if the learner's last turn contained any English word."
                    ),
                },
            },
            "required": [
                "learner_utterance",
                "errors",
                "fluency_signal",
                "L1_used",
            ],
            "additionalProperties": False,
        },
    },
}

HABLE_YA_TOOLS: list[dict[str, object]] = [LOG_TURN_TOOL]
