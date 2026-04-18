"""Convert eval fixtures to SFT training format.

Produces JSONL-compatible dicts matching the format in README.md.
"""
from __future__ import annotations

import json
from typing import Any

from eval.fixtures.schema import CATEGORY_FILES, CEFRBand, Fixture, SystemParams

# Canonical log_turn argument keys (training target schema).
_CANONICAL_KEYS = ("learner_utterance", "errors", "fluency_signal", "L1_used")
_CANONICAL_ERROR_KEYS = ("type", "produced", "target")

# Per-band register guidance derived from eval/scoring/register.py thresholds.
# Keep numbers aligned with that module so what the model is told matches what
# the scorer actually measures.
_REGISTER_GUIDANCE: dict[str, str] = {
    "A1": (
        "Very short sentences (max 10 words each). Only basic, common "
        "vocabulary. Keep words short — prefer 1-2 syllable words."
    ),
    "A2": (
        "Short sentences (max 14 words each). Everyday vocabulary; avoid "
        "long or technical words."
    ),
    "B1": (
        "Conversational sentences (max 18 words each). Common vocabulary "
        "with occasional less-frequent words."
    ),
    "B2": (
        "Natural conversational sentences (max 22 words each). Most "
        "vocabulary is fine; avoid academic or highly specialised jargon."
    ),
    "C1": (
        "Native-level register. Complex sentences are fine (max 30 words). "
        "Rich and varied vocabulary welcome."
    ),
}

# One worked recast example per band — chosen so the register of the
# example itself roughly matches the target band (so the model is not
# inadvertently primed with A1 complexity on a C1 turn).
_RECAST_EXAMPLES: dict[str, tuple[str, str]] = {
    "A1": (
        "Yo es Juan.",
        "Hola Juan, yo soy María. ¿De dónde eres?",
    ),
    "A2": (
        "El parque es cerca de mi casa.",
        "Ah, el parque está cerca de tu casa. ¿Vas a menudo?",
    ),
    "B1": (
        "Ayer yo fui al cine y he visto una película muy buena.",
        "Qué bien que viste una película buena ayer. ¿De qué trataba?",
    ),
    "B2": (
        "Si tendría más tiempo, viajaría a México.",
        "Claro, si tuvieras más tiempo viajarías a México. ¿Qué región te atrae más?",
    ),
    "C1": (
        "Me molesta que siempre llega tarde a las reuniones.",
        "Entiendo que te moleste que llegue tarde — es frustrante. ¿Has hablado con él?",
    ),
}

# Phrases the recast scorer flags as explicit correction (eval/scoring/recast.py).
# Listed verbatim in the prompt so the model knows exactly what to avoid.
_FORBIDDEN_CORRECTION_PHRASES: tuple[str, ...] = (
    "no se dice",
    "la forma correcta",
    "debes decir",
    "deberías decir",
    "se dice",
    "la palabra correcta",
    "en realidad es",
    "quieres decir",
    "quisiste decir",
    "correcto es",
)


def _band_from_production_level(level: float) -> CEFRBand:
    if level < 0.2:
        return "A1"
    if level < 0.4:
        return "A2"
    if level < 0.6:
        return "B1"
    if level < 0.8:
        return "B2"
    return "C1"

def _render_system_prompt(
    params: SystemParams,
    band: CEFRBand | None = None,
) -> str:
    """Build the runtime/fine-tune system prompt from fixture system_params.

    ``band`` overrides the production-level-derived band. Pass
    ``fixture.metadata.cefr_band`` when available so the prompt's register
    guidance matches what the scorer actually grades against.

    TODO: unify with hable-ya/pipeline/prompts/builder.py once that is
    implemented.  For now this is the single source used by both
    ``eval.run_eval`` and ``finetune.generate`` — keeping the two in lockstep.
    """
    p = params.profile
    t = params.theme
    if band is None:
        band = _band_from_production_level(p.production_level)

    lines: list[str] = [
        "You are a Spanish conversation partner for a language learner.",
        "",
        "## Learner",
        f"- CEFR level: {band}",
        f"- L1 reliance: {p.L1_reliance:.2f} "
        "(higher = more likely to fall back on English)",
        f"- Speech fluency: {p.speech_fluency:.2f}",
    ]
    if p.error_patterns:
        lines.append(f"- Known error patterns: {', '.join(p.error_patterns)}")
    if p.vocab_strengths:
        lines.append(f"- Vocabulary strengths: {', '.join(p.vocab_strengths)}")

    lines.extend([
        "",
        f"## Topic: {t.domain}",
        t.prompt,
    ])
    if t.target_structures:
        lines.append(f"Target structures: {', '.join(t.target_structures)}.")

    lines.extend([
        "",
        "## Response format (strict)",
        "- 1 to 3 sentences total.",
        "- Exactly one question mark.",
        "- Spanish only. Do not use any English words — not even "
        '"sorry", "yes", "no", "I", "ok".',
        f"- {band} register: {_REGISTER_GUIDANCE[band]}",
        "",
        "## Handling learner errors: recast, never correct",
        "If the learner's last turn contains an error, include the CORRECT "
        "form naturally in your reply — as if paraphrasing what they meant. "
        "Do not mention that anything was wrong.",
        "",
        "Never use any of these phrases (they count as explicit correction):",
    ])
    for phrase in _FORBIDDEN_CORRECTION_PHRASES:
        lines.append(f'  "{phrase}"')
    lines.append(
        "Also never use the Spanish word 'error' when talking to the learner."
    )

    lines.extend([
        "",
        "Never echo the learner's incorrect form. If you need to refer "
        "back to what they said, use the corrected version.",
        "",
        "### Example",
    ])
    learner_ex, assistant_ex = _RECAST_EXAMPLES[band]
    lines.extend([
        f'Learner: "{learner_ex}"',
        f'You: "{assistant_ex}"',
        "",
        "## After your reply, call log_turn",
        "After your Spanish reply, emit the tool call on its own line in "
        "this EXACT format — no code fences, no other wrappers:",
        "",
        'log_turn({"learner_utterance": "...", "errors": [...], '
        '"fluency_signal": "...", "L1_used": ...})',
        "",
        "Use that exact function-call shape: the literal name `log_turn`, "
        "an opening paren, a single JSON object, a closing paren. Do not "
        "wrap it in code fences or add `<tool_call>` tags.",
        "",
        "Arguments (all required):",
        "- learner_utterance: the learner's last message copied VERBATIM.",
        "- errors: list of {type, produced, target} objects. "
        '"type" is the error category (e.g. "ser_estar"); '
        '"produced" is the wrong form the learner used; '
        '"target" is the corrected form you wove into your reply. '
        "Use [] if there were no errors.",
        '- fluency_signal: "weak", "moderate", or "strong" — based on the '
        "learner's last message (hesitations, fragments, short utterances → "
        "weak; complete sentences with some errors → moderate; fluent and "
        "extended → strong).",
        "- L1_used: true if the learner's last message contained any "
        "English word, otherwise false.",
        "",
        "### Full-turn example",
        f'Learner: "{learner_ex}"',
        "You:",
        assistant_ex,
        'log_turn({"learner_utterance": "'
        + learner_ex
        + '", "errors": [], "fluency_signal": "moderate", "L1_used": false})',
    ])

    return "\n".join(lines)


def _last_user_utterance(fixture: Fixture) -> str:
    for turn in reversed(fixture.conversation):
        if turn.role == "user":
            return turn.content
    return ""


def _normalize_error_item(raw: Any, fallback_type: str | None = None) -> dict[str, str] | None:
    """Coerce any fixture-format error entry into {type, produced, target}.

    Accepts dicts with assorted key names, or strings (e.g. ``"ser_estar"`` or
    ``"vowel_substitution:levento"``). Returns ``None`` if nothing usable.
    """
    if isinstance(raw, dict):
        err_type = (
            raw.get("type")
            or raw.get("pattern")
            or raw.get("error_type")
            or fallback_type
            or ""
        )
        produced = (
            raw.get("produced")
            or raw.get("error_form")
            or raw.get("form")
            or ""
        )
        target = (
            raw.get("target")
            or raw.get("correct_form")
            or raw.get("correction")
            or ""
        )
        if not err_type and not produced:
            return None
        return {"type": str(err_type), "produced": str(produced), "target": str(target)}

    if isinstance(raw, str):
        if ":" in raw:
            err_type, produced = raw.split(":", 1)
            return {"type": err_type.strip(), "produced": produced.strip(), "target": ""}
        return {"type": raw.strip(), "produced": "", "target": ""}

    return None


def _normalize_log_turn_args(fixture: Fixture, raw_args: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical ``log_turn`` payload: {learner_utterance, errors, fluency_signal, L1_used}.

    Prefers structured data from ``raw_args`` when present; otherwise falls back
    to ``metadata`` (errors_present, expected_recast, fluency_signal, L1_used).
    """
    # Anchor on the conversation's last user turn — that's the source of truth
    # for `eval/scoring/turn.py` (tool_args_correct requires exact match).
    utterance = _last_user_utterance(fixture) or raw_args.get("learner_utterance", "")

    # Gather errors from whichever key the fixture used.
    source_errors: list[Any] = []
    for key in ("errors", "errors_observed", "errors_detected"):
        val = raw_args.get(key)
        if isinstance(val, list) and val:
            source_errors = val
            break

    meta_types = list(fixture.metadata.errors_present)
    errors: list[dict[str, str]] = []
    for idx, item in enumerate(source_errors):
        fallback_type = meta_types[idx] if idx < len(meta_types) else None
        norm = _normalize_error_item(item, fallback_type=fallback_type)
        if norm is not None:
            errors.append(norm)

    return {
        "learner_utterance": utterance,
        "errors": errors,
        "fluency_signal": fixture.metadata.fluency_signal,
        "L1_used": fixture.metadata.L1_used,
    }


def _render_tool_call(name: str, args: dict[str, Any]) -> str:
    """Render a tool call as ``name({...json...})``.

    The base Gemma model has strong priors for this function-call style and
    consistently produces it post-fine-tune; matching that here means the
    training signal reinforces what the model wants to do anyway. The eval
    parser at ``eval/scoring/turn.py`` accepts both this and the legacy
    ``[TOOL_CALL: name]{...}`` form for backward compatibility.
    """
    # Serialise with stable key order so the model sees one layout.
    ordered = {k: args[k] for k in _CANONICAL_KEYS if k in args}
    for k, v in args.items():
        if k not in ordered:
            ordered[k] = v
    return f"\n\n{name}({json.dumps(ordered, ensure_ascii=False)})"


def _canonical_log_turn(fixture: Fixture) -> dict[str, Any]:
    """Build the canonical log_turn args from the fixture, preferring its own tool call."""
    for tc in fixture.expected.tool_calls:
        if tc.name == "log_turn":
            return _normalize_log_turn_args(fixture, tc.arguments)
    return _normalize_log_turn_args(fixture, {})


def _build_assistant_content(fixture: Fixture) -> str:
    """Assemble the target assistant turn: response text + a canonical log_turn call.

    ``log_error`` and other auxiliary tool calls are intentionally dropped so
    the fine-tune target is a single consistent shape.
    """
    args = _canonical_log_turn(fixture)
    return fixture.expected.response_text + _render_tool_call("log_turn", args)


def _extract_category(fixture_id: str) -> str:
    """Derive category from fixture id prefix."""
    for cat in sorted(CATEGORY_FILES, key=len, reverse=True):
        if fixture_id.startswith(cat):
            return cat
    return "unknown"


def fixture_to_sft(fixture: Fixture) -> dict[str, Any]:
    """Convert a standard Fixture to SFT training format.

    Returns a dict with ``messages`` and ``metadata`` keys, ready for
    JSONL serialisation.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _render_system_prompt(fixture.system_params, band=fixture.metadata.cefr_band)},
    ]

    for turn in fixture.conversation:
        messages.append({"role": turn.role, "content": turn.content})

    messages.append({"role": "assistant", "content": _build_assistant_content(fixture)})

    metadata: dict[str, Any] = {
        "category": _extract_category(fixture.id),
        "cefr_band": fixture.metadata.cefr_band,
        "error_type": fixture.metadata.errors_present[0]
        if fixture.metadata.errors_present
        else None,
        "difficulty": fixture.metadata.difficulty,
        "weight": 1.0,
    }

    return {"messages": messages, "metadata": metadata}
