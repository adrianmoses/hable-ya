"""build_system_prompt() output tests."""
from __future__ import annotations

from finetune.format import FORBIDDEN_CORRECTION_PHRASES
from hable_ya.pipeline.prompts.builder import (
    PLACEHOLDER_SYSTEM_PROMPT,
    build_system_prompt,
)


def test_prompt_is_non_empty_spanish() -> None:
    prompt = build_system_prompt({"band": "A2", "learner_id": "x"})
    assert prompt
    assert "español" in prompt.lower()


def test_prompt_asks_for_tool_call() -> None:
    prompt = build_system_prompt({"band": "A2", "learner_id": "x"})
    assert "[TOOL_CALL: log_turn]" in prompt


def test_prompt_does_not_use_forbidden_phrases_in_instructions() -> None:
    # The prompt lists the forbidden phrases as things to avoid — they appear
    # quoted inside the `- "..."` list. Outside that list, none of them should
    # appear as natural prose.
    prompt = build_system_prompt({"band": "A2", "learner_id": "x"})
    # Strip the block of quoted forbidden phrases and assert what remains has none.
    lines = prompt.splitlines()
    non_list_prose = "\n".join(
        line for line in lines if not line.strip().startswith('- "')
    )
    for phrase in FORBIDDEN_CORRECTION_PHRASES:
        assert phrase not in non_list_prose.lower(), (
            f"forbidden phrase {phrase!r} appears in prompt prose"
        )


def test_placeholder_constant_matches_builder_output() -> None:
    # The builder currently ignores learner details; assert the coupling so
    # spec 023 can swap both without drift.
    assert build_system_prompt({"band": "A2"}) == PLACEHOLDER_SYSTEM_PROMPT
