"""Runtime system-prompt builder.

Async wrapper over :func:`hable_ya.pipeline.prompts.render.render_system_prompt`.
On cold start (no pool, or ``sessions_completed == 0``) it returns the same
bytes the pre-029 neutral builder produced — spec-023 byte-identity tests in
``tests/test_prompts.py`` stay green. Once the learner has completed a
session, it pulls a :class:`LearnerProfileSnapshot` from the DB and maps it
into a :class:`LearnerProfile`, and picks a :class:`Theme` from
:mod:`hable_ya.learner.themes` that honours the cooldown window.

``build_system_prompt`` returns just the rendered string (used in tests and
cold paths). ``build_session_prompt`` returns a :class:`SessionPrompt` with
the same rendered string plus the resolved theme and band — the session
handler needs both to thread through to ``TurnIngestService.start_session``.
"""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from eval.fixtures.schema import CEFRBand, LearnerProfile, SystemParams, Theme
from hable_ya.config import settings
from hable_ya.learner.profile import LearnerProfileRepo, LearnerProfileSnapshot
from hable_ya.learner.themes import NEUTRAL_THEME as _NEUTRAL_THEME
from hable_ya.learner.themes import get_session_theme
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


@dataclass(slots=True, frozen=True)
class SessionPrompt:
    text: str
    theme: Theme
    band: CEFRBand


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


def _profile_from_snapshot(snapshot: LearnerProfileSnapshot) -> LearnerProfile:
    level = _BAND_MIDPOINT.get(snapshot.band, 0.5)
    return LearnerProfile(
        production_level=level,
        L1_reliance=snapshot.l1_reliance,
        speech_fluency=snapshot.speech_fluency,
        is_calibrated=snapshot.sessions_completed > 0,
        sessions_completed=snapshot.sessions_completed,
        vocab_strengths=list(snapshot.vocab_strengths),
        error_patterns=list(snapshot.error_patterns),
    )


async def build_session_prompt(
    learner: dict[str, object],
    *,
    pool: asyncpg.Pool | None = None,
    recent_domains: list[str] | None = None,
) -> SessionPrompt:
    opt_in_cold_start = bool(learner.get("cold_start"))

    if pool is None:
        band_raw = learner.get("band", "A2")
        band: CEFRBand = band_raw if isinstance(band_raw, str) else "A2"  # type: ignore[assignment]
        profile = _neutral_profile(band)
        theme = _NEUTRAL_THEME
    else:
        snapshot = await LearnerProfileRepo(pool).get(
            window_turns=settings.profile_window_turns,
            top_errors=settings.profile_top_errors,
            top_vocab=settings.profile_top_vocab,
        )
        band = snapshot.band
        profile = _profile_from_snapshot(snapshot)
        first_session = snapshot.sessions_completed == 0
        # First session with a real pool = cold start regardless of the flag.
        opt_in_cold_start = opt_in_cold_start or first_session
        theme = (
            _NEUTRAL_THEME
            if first_session
            else get_session_theme(
                level=band,
                recent_domains=recent_domains or [],
                cooldown=settings.theme_cooldown,
            )
        )

    params = SystemParams(profile=profile, theme=theme)
    rendered = render_system_prompt(params, band=band)
    if opt_in_cold_start:
        rendered = f"{rendered}\n\n## Primera sesión\n{COLD_START_INSTRUCTIONS}"
    return SessionPrompt(text=rendered, theme=theme, band=band)


async def build_system_prompt(
    learner: dict[str, object],
    *,
    pool: asyncpg.Pool | None = None,
    recent_domains: list[str] | None = None,
) -> str:
    session_prompt = await build_session_prompt(
        learner, pool=pool, recent_domains=recent_domains
    )
    return session_prompt.text
