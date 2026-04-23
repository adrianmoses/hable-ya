"""LearnerProfileRepo — snapshot + mutations for the single-tenant profile row.

The snapshot is the handoff object the prompt builder consumes. It combines
the static `learner_profile` row with two rolling-window aggregates derived
from the last K=20 turns (`L1_reliance`, `speech_fluency`) and the top-N
reads from `error_counts` / `vocabulary_items`. The actual mapping from
snapshot → :class:`eval.fixtures.schema.LearnerProfile` happens in the
prompt builder at render time; this module only owns persistence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import asyncpg

from eval.fixtures.schema import CEFRBand

logger = logging.getLogger(__name__)

_FLUENCY_TO_FLOAT = {"weak": 0.3, "moderate": 0.6, "strong": 0.9}


@dataclass(slots=True, frozen=True)
class LearnerProfileSnapshot:
    band: CEFRBand
    sessions_completed: int
    # Rolling-mean over the last N turns. Neutral default (0.5) when there is
    # no history, so cold-start renders identically to the pre-029 neutral
    # profile.
    l1_reliance: float = 0.5
    speech_fluency: float = 0.5
    error_patterns: list[str] = field(default_factory=list)
    vocab_strengths: list[str] = field(default_factory=list)


class LearnerProfileRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(
        self,
        *,
        window_turns: int = 20,
        top_errors: int = 3,
        top_vocab: int = 5,
    ) -> LearnerProfileSnapshot:
        async with self._pool.acquire() as conn:
            profile_row = await conn.fetchrow(
                "SELECT band, sessions_completed FROM learner_profile WHERE id = 1"
            )
            if profile_row is None:
                logger.warning(
                    "learner_profile row missing — returning neutral snapshot"
                )
                return LearnerProfileSnapshot(band="A2", sessions_completed=0)

            band: CEFRBand = profile_row["band"]
            sessions_completed = int(profile_row["sessions_completed"])

            agg_row = await conn.fetchrow(
                """
                SELECT
                    AVG(CASE WHEN L1_used THEN 1.0 ELSE 0.0 END) AS l1_mean,
                    AVG(
                        CASE fluency_signal
                            WHEN 'weak'     THEN $2::float8
                            WHEN 'moderate' THEN $3::float8
                            WHEN 'strong'   THEN $4::float8
                        END
                    ) AS fluency_mean
                FROM (
                    SELECT L1_used, fluency_signal
                    FROM turns
                    ORDER BY timestamp DESC
                    LIMIT $1
                ) recent
                """,
                window_turns,
                _FLUENCY_TO_FLOAT["weak"],
                _FLUENCY_TO_FLOAT["moderate"],
                _FLUENCY_TO_FLOAT["strong"],
            )
            l1_mean = agg_row["l1_mean"] if agg_row is not None else None
            fluency_mean = agg_row["fluency_mean"] if agg_row is not None else None

            error_rows = await conn.fetch(
                """
                SELECT category FROM error_counts
                ORDER BY count DESC, last_seen_at DESC
                LIMIT $1
                """,
                top_errors,
            )
            vocab_rows = await conn.fetch(
                """
                SELECT lemma FROM vocabulary_items
                ORDER BY last_seen_at DESC
                LIMIT $1
                """,
                top_vocab,
            )

        return LearnerProfileSnapshot(
            band=band,
            sessions_completed=sessions_completed,
            l1_reliance=float(l1_mean) if l1_mean is not None else 0.5,
            speech_fluency=float(fluency_mean) if fluency_mean is not None else 0.5,
            error_patterns=[r["category"] for r in error_rows],
            vocab_strengths=[r["lemma"] for r in vocab_rows],
        )

    async def increment_session_count(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE learner_profile
                SET sessions_completed = sessions_completed + 1,
                    updated_at = now()
                WHERE id = 1
                """
            )

    async def set_band(self, band: CEFRBand) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE learner_profile SET band = $1, updated_at = now() WHERE id = 1",
                band,
            )
