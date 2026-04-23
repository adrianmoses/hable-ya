"""End-to-end ingestion: TurnObservation → relational + AGE graph."""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest_asyncio

from hable_ya.learner import graph
from hable_ya.learner.ingest import TurnIngestService
from hable_ya.runtime.observations import TurnObservation


@pytest_asyncio.fixture
async def ingest_ready(clean_learner_state: asyncpg.Pool) -> asyncpg.Pool:
    """On top of the shared clean state, seed the `Learner` graph node so
    downstream MATCH clauses resolve."""
    async with clean_learner_state.acquire() as conn:
        await graph.ensure_learner_node(conn)
    return clean_learner_state


async def _count(pool: asyncpg.Pool, cypher_body: str) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM cypher('{graph.GRAPH}', $$ {cypher_body} $$) "
            f"AS (c ag_catalog.agtype)"
        )
    assert row is not None
    return int(str(row["c"]))


def _obs(
    *,
    session_id: str = "int_session_1",
    utterance: str = "quiero comer manzanas todos los días",
    errors: list[dict[str, str]] | None = None,
    fluency: str = "moderate",
    l1: bool = False,
    at: datetime | None = None,
) -> TurnObservation:
    at = at or datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    return TurnObservation(
        session_id=session_id,
        timestamp_iso=at.isoformat(),
        learner_utterance=utterance,
        errors=errors or [],
        fluency_signal=fluency,
        L1_used=l1,
    )


async def test_ingest_populates_turns_errors_vocab_and_graph(
    ingest_ready: asyncpg.Pool,
) -> None:
    ingest = TurnIngestService(ingest_ready)
    at = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    obs = _obs(
        errors=[{"type": "ser_estar", "produced_form": "soy", "target_form": "estoy"}],
        at=at,
    )
    await ingest.ingest(obs)

    async with ingest_ready.acquire() as conn:
        turn_rows = await conn.fetch("SELECT session_id FROM turns")
        error_rows = await conn.fetch("SELECT category, count FROM error_counts")
        vocab_rows = await conn.fetch(
            "SELECT lemma, production_count FROM vocabulary_items"
        )

    assert len(turn_rows) == 1
    assert turn_rows[0]["session_id"] == "int_session_1"
    assert {(r["category"], r["count"]) for r in error_rows} == {("ser_estar", 1)}
    vocab_map = {r["lemma"]: r["production_count"] for r in vocab_rows}
    assert {"querer", "comer", "manzana", "día"} <= set(vocab_map)

    err_nodes = await _count(
        ingest_ready,
        "MATCH (:Learner)-[:MADE_ERROR]->(e:ErrorPattern {category: 'ser_estar'}) "
        "RETURN count(e)",
    )
    assert err_nodes == 1
    vocab_edges = await _count(
        ingest_ready,
        "MATCH (:Learner)-[:PRODUCED]->(v:VocabItem) RETURN count(v)",
    )
    assert vocab_edges >= 4


async def test_second_turn_increments_without_duplication(
    ingest_ready: asyncpg.Pool,
) -> None:
    ingest = TurnIngestService(ingest_ready)
    at = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    err = [{"type": "ser_estar", "produced_form": "a", "target_form": "b"}]
    await ingest.ingest(_obs(errors=err, at=at))
    await ingest.ingest(_obs(errors=err, at=at.replace(minute=5)))

    async with ingest_ready.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count FROM error_counts WHERE category = 'ser_estar'"
        )
        turns = await conn.fetchval("SELECT count(*) FROM turns")
    assert count == 2
    assert turns == 2

    vocab_count = await _count(
        ingest_ready,
        "MATCH (v:VocabItem {lemma: 'comer'}) RETURN v.production_count",
    )
    assert vocab_count == 2


async def test_start_session_creates_session_row_and_scenario_edge(
    clean_learner_state: asyncpg.Pool,
) -> None:
    ingest = TurnIngestService(clean_learner_state)
    await ingest.start_session(
        session_id="sess_x",
        theme_domain="pedir un café",
        band="A1",
    )
    async with clean_learner_state.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT theme_domain, band_at_start FROM sessions "
            "WHERE session_id = 'sess_x'"
        )
        sessions_completed = await conn.fetchval(
            "SELECT sessions_completed FROM learner_profile WHERE id = 1"
        )
    assert row is not None
    assert row["theme_domain"] == "pedir un café"
    assert row["band_at_start"] == "A1"
    assert sessions_completed == 1

    scenario_edges = await _count(
        clean_learner_state,
        "MATCH (:Learner)-[:ENGAGED_WITH]->(:Scenario {domain: 'pedir un café'}) "
        "RETURN count(*)",
    )
    assert scenario_edges == 1


async def test_end_session_sets_ended_at(clean_learner_state: asyncpg.Pool) -> None:
    ingest = TurnIngestService(clean_learner_state)
    await ingest.start_session(
        session_id="sess_end",
        theme_domain="pedir un café",
        band="A1",
    )
    await ingest.end_session(session_id="sess_end")
    async with clean_learner_state.acquire() as conn:
        ended = await conn.fetchval(
            "SELECT ended_at FROM sessions WHERE session_id = 'sess_end'"
        )
    assert ended is not None
