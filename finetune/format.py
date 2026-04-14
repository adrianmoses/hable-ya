"""Convert eval fixtures to SFT / DPO training format.

Produces JSONL-compatible dicts matching the formats in README.md.
"""
from __future__ import annotations

import json
from typing import Any

from eval.fixtures.schema import CATEGORY_FILES, Fixture, SystemParams


def _render_system_prompt(params: SystemParams) -> str:
    """Build a system prompt from fixture system_params.

    TODO: unify with hable-ya/pipeline/prompts/builder.py once that is
    implemented.  For now this is a self-contained template that captures
    the learner profile and theme — enough for fine-tuning examples.
    """
    p = params.profile
    t = params.theme

    # Map production_level to approximate CEFR band
    band = (
        "A1"
        if p.production_level < 0.2
        else "A2"
        if p.production_level < 0.4
        else "B1"
        if p.production_level < 0.6
        else "B2"
        if p.production_level < 0.8
        else "C1"
    )

    lines = [
        "You are a Spanish conversation partner for language learners.",
        f"The learner is at CEFR level {band} (production_level={p.production_level:.2f}).",
        f"L1 reliance: {p.L1_reliance:.2f}. Speech fluency: {p.speech_fluency:.2f}.",
    ]
    if p.error_patterns:
        lines.append(f"Known error patterns: {', '.join(p.error_patterns)}.")
    if p.vocab_strengths:
        lines.append(f"Vocabulary strengths: {', '.join(p.vocab_strengths)}.")

    lines.append("")
    lines.append(f"Topic: {t.domain}.")
    lines.append(f"Instructions: {t.prompt}")
    if t.target_structures:
        lines.append(
            f"Target structures: {', '.join(t.target_structures)}."
        )

    lines.append("")
    lines.append(
        "When the learner makes an error, recast it naturally in your "
        "response — never correct explicitly.  Keep responses to 1-3 "
        "sentences with exactly one question.  After responding, call "
        "log_turn with the learner utterance, errors, and fluency "
        "indicators."
    )
    return "\n".join(lines)


def _build_assistant_content(fixture: Fixture) -> str:
    """Assemble the target assistant turn: response text + inline tool calls."""
    parts = [fixture.expected.response_text]
    for tc in fixture.expected.tool_calls:
        parts.append(
            f"\n\n[TOOL_CALL: {tc.name}]{json.dumps(tc.arguments, ensure_ascii=False)}"
        )
    return "".join(parts)


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
        {"role": "system", "content": _render_system_prompt(fixture.system_params)},
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


def fixture_to_dpo(fixture: Fixture) -> list[dict[str, Any]]:
    """Convert a standard Fixture to DPO contrast pairs.

    Returns one pair per ``negative_example``.  Each pair has ``messages``
    (the context), ``chosen`` (the correct response), ``rejected`` (the bad
    response), and ``metadata``.
    """
    context: list[dict[str, str]] = [
        {"role": "system", "content": _render_system_prompt(fixture.system_params)},
    ]
    for turn in fixture.conversation:
        context.append({"role": turn.role, "content": turn.content})

    chosen = {
        "role": "assistant",
        "content": _build_assistant_content(fixture),
    }

    pairs: list[dict[str, Any]] = []
    for neg in fixture.negative_examples:
        pairs.append(
            {
                "messages": context,
                "chosen": chosen,
                "rejected": {"role": "assistant", "content": neg.response},
                "metadata": {
                    "rejection_reason": neg.label,
                    "cefr_band": fixture.metadata.cefr_band,
                },
            }
        )
    return pairs
