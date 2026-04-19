"""build_system_prompt() output tests."""
from __future__ import annotations

import pytest

from eval.fixtures.schema import CEFRBand, LearnerProfile, SystemParams, Theme
from hable_ya.pipeline.prompts.builder import (
    _BAND_MIDPOINT,
    _NEUTRAL_THEME,
    build_system_prompt,
)
from hable_ya.pipeline.prompts.register import (
    COLD_START_INSTRUCTIONS,
    REGISTER_BY_LEVEL,
)
from hable_ya.pipeline.prompts.render import (
    FORBIDDEN_CORRECTION_PHRASES,
    REGISTER_GUIDANCE,
    render_system_prompt,
)

BANDS: tuple[CEFRBand, ...] = ("A1", "A2", "B1", "B2", "C1")


def _expected_params(band: CEFRBand) -> SystemParams:
    profile = LearnerProfile(
        production_level=_BAND_MIDPOINT[band],
        L1_reliance=0.5,
        speech_fluency=0.5,
        is_calibrated=False,
        sessions_completed=0,
        vocab_strengths=[],
        error_patterns=[],
    )
    return SystemParams(profile=profile, theme=_NEUTRAL_THEME)


@pytest.mark.parametrize("band", BANDS)
def test_builder_matches_render_for_band(band: CEFRBand) -> None:
    expected = render_system_prompt(_expected_params(band), band=band)
    assert build_system_prompt({"band": band}) == expected


def test_builder_defaults_to_a2() -> None:
    expected = render_system_prompt(_expected_params("A2"), band="A2")
    assert build_system_prompt({}) == expected


def test_prompt_is_non_empty_and_about_spanish() -> None:
    prompt = build_system_prompt({"band": "A2"})
    assert prompt
    assert "Spanish" in prompt


def test_prompt_contains_canonical_log_turn_example() -> None:
    prompt = build_system_prompt({"band": "A2"})
    assert (
        'log_turn({"learner_utterance": "...", "errors": [...], '
        '"fluency_signal": "...", "L1_used": ...})'
    ) in prompt


def test_prompt_lists_forbidden_phrases() -> None:
    prompt = build_system_prompt({"band": "A2"})
    for phrase in FORBIDDEN_CORRECTION_PHRASES:
        assert phrase in prompt


def test_prompt_does_not_use_forbidden_phrases_in_instructions() -> None:
    # Forbidden phrases appear quoted inside `  "..."` list items. Outside
    # that list, none should appear as natural prose.
    prompt = build_system_prompt({"band": "A2"})
    non_list_prose = "\n".join(
        line for line in prompt.splitlines() if not line.strip().startswith('"')
    )
    for phrase in FORBIDDEN_CORRECTION_PHRASES:
        assert phrase not in non_list_prose.lower()


@pytest.mark.parametrize("band", BANDS)
def test_prompt_uses_band_register_guidance(band: CEFRBand) -> None:
    prompt = build_system_prompt({"band": band})
    assert REGISTER_GUIDANCE[band] in prompt


def test_register_by_level_matches_render_module() -> None:
    assert REGISTER_BY_LEVEL == dict(REGISTER_GUIDANCE)


def test_cold_start_opt_in_appends_guidance() -> None:
    warm = build_system_prompt({"band": "A2"})
    cold = build_system_prompt({"band": "A2", "cold_start": True})
    assert COLD_START_INSTRUCTIONS in cold
    assert COLD_START_INSTRUCTIONS not in warm
    assert cold.startswith(warm)


def test_cold_start_instructions_non_empty() -> None:
    assert COLD_START_INSTRUCTIONS.strip() != ""


def test_theme_is_neutral() -> None:
    assert isinstance(_NEUTRAL_THEME, Theme)
    assert _NEUTRAL_THEME.domain == "conversación abierta"
