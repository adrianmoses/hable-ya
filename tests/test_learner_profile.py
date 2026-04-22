"""LearnerProfileRepo — snapshot read + mutations."""
from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from hable_ya.learner.profile import LearnerProfileRepo


async def _insert_session(
    pool: asyncpg.Pool, session_id: str = "s1", band: str = "A2"
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (session_id, band_at_start) VALUES ($1, $2)",
            session_id,
            band,
        )


async def _insert_turn(
    pool: asyncpg.Pool,
    *,
    session_id: str,
    at: datetime,
    fluency: str,
    l1_used: bool,
    utterance: str = "x",
) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                INSERT INTO turns
                    (session_id, timestamp, learner_utterance, fluency_signal, L1_used)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                session_id,
                at,
                utterance,
                fluency,
                l1_used,
            )
        )


async def test_cold_start_snapshot_uses_neutral_defaults(
    clean_learner_state: asyncpg.Pool,
) -> None:
    repo = LearnerProfileRepo(clean_learner_state)
    snapshot = await repo.get()
    assert snapshot.band == "A2"
    assert snapshot.sessions_completed == 0
    assert snapshot.l1_reliance == 0.5
    assert snapshot.speech_fluency == 0.5
    assert snapshot.error_patterns == []
    assert snapshot.vocab_strengths == []


async def test_rolling_means_reflect_recent_turns(
    clean_learner_state: asyncpg.Pool,
) -> None:
    await _insert_session(clean_learner_state)
    base = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    rows = [
        ("weak", True),     # 0.3, 1.0
        ("weak", False),    # 0.3, 0.0
        ("moderate", True), # 0.6, 1.0
        ("strong", False),  # 0.9, 0.0
        ("strong", False),  # 0.9, 0.0
    ]
    for i, (fluency, l1) in enumerate(rows):
        at = base.replace(minute=i)
        await _insert_turn(
            clean_learner_state, session_id="s1", at=at, fluency=fluency, l1_used=l1
        )

    repo = LearnerProfileRepo(clean_learner_state)
    snapshot = await repo.get(window_turns=20)
    expected_l1 = 2 / 5
    expected_fluency = (0.3 + 0.3 + 0.6 + 0.9 + 0.9) / 5
    assert snapshot.l1_reliance == pytest.approx(expected_l1)
    assert snapshot.speech_fluency == pytest.approx(expected_fluency)


async def test_window_limits_to_most_recent_turns(
    clean_learner_state: asyncpg.Pool,
) -> None:
    await _insert_session(clean_learner_state)
    base = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    # Insert one strong+no-L1 old turn, then 3 weak+L1 recent turns
    await _insert_turn(
        clean_learner_state, session_id="s1", at=base.replace(hour=10),
        fluency="strong", l1_used=False,
    )
    for i in range(3):
        await _insert_turn(
            clean_learner_state, session_id="s1", at=base.replace(minute=i),
            fluency="weak", l1_used=True,
        )

    repo = LearnerProfileRepo(clean_learner_state)
    snapshot = await repo.get(window_turns=3)
    # Only the 3 recent ones should count: all L1_used=True, all weak.
    assert snapshot.l1_reliance == pytest.approx(1.0)
    assert snapshot.speech_fluency == pytest.approx(0.3)


async def test_top_errors_reflect_error_counts_ordering(
    clean_learner_state: asyncpg.Pool,
) -> None:
    at = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    async with clean_learner_state.acquire() as conn:
        await conn.execute(
            "INSERT INTO error_counts (category, count, last_seen_at) VALUES "
            "('ser_estar', 5, $1), ('agreement', 3, $1), ('tense', 1, $1), "
            "('prepositions', 4, $1)",
            at,
        )

    repo = LearnerProfileRepo(clean_learner_state)
    snapshot = await repo.get(top_errors=3)
    assert snapshot.error_patterns == ["ser_estar", "prepositions", "agreement"]


async def test_top_vocab_reflects_last_seen_ordering(
    clean_learner_state: asyncpg.Pool,
) -> None:
    async with clean_learner_state.acquire() as conn:
        base = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
        for i, lemma in enumerate(["uno", "dos", "tres", "cuatro", "cinco", "seis"]):
            last_seen = base.replace(minute=i)
            await conn.execute(
                "INSERT INTO vocabulary_items (lemma, sample_form, "
                "production_count, first_seen_at, last_seen_at) "
                "VALUES ($1, $1, 1, $2, $2)",
                lemma,
                last_seen,
            )

    repo = LearnerProfileRepo(clean_learner_state)
    snapshot = await repo.get(top_vocab=3)
    assert snapshot.vocab_strengths == ["seis", "cinco", "cuatro"]


async def test_increment_session_count(clean_learner_state: asyncpg.Pool) -> None:
    repo = LearnerProfileRepo(clean_learner_state)
    before = (await repo.get()).sessions_completed
    await repo.increment_session_count()
    await repo.increment_session_count()
    after = (await repo.get()).sessions_completed
    assert after == before + 2


async def test_set_band_persists(clean_learner_state: asyncpg.Pool) -> None:
    repo = LearnerProfileRepo(clean_learner_state)
    await repo.set_band("B1")
    snapshot = await repo.get()
    assert snapshot.band == "B1"


