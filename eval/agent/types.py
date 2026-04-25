"""Shared dataclass-like types for the agent-eval orchestrator.

`ConversationTurn` is reused from `eval.fixtures.schema`. `TurnRecord` is
the in-memory shape the `ProfileAccumulator` keeps for each agent turn —
it mirrors what `LearnerProfileRepo` reads back from the `turns` table
plus the parsed-out errors/vocab so the accumulator can update its
counters in place.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eval.fixtures.schema import (
    CEFRBand,
    ConversationTurn,
    FluencySignal,
)

__all__ = [
    "AgentEvalOutput",
    "ConversationTurn",
    "SessionRecord",
    "TurnRecord",
]


class TurnRecord(BaseModel):
    """One agent-side turn's signals, shaped to feed the accumulator."""

    model_config = ConfigDict(extra="forbid")

    fluency_signal: FluencySignal
    L1_used: bool
    error_categories: list[str] = Field(default_factory=list)
    vocab_lemmas: list[str] = Field(default_factory=list)


class SessionRecord(BaseModel):
    """One persona's full session: transcript + per-turn signals + verdict."""

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    cefr_band: CEFRBand
    scenario_domain: str
    transcript: list[ConversationTurn]
    turn_records: list[TurnRecord]
    verdict: dict[str, Any]
    model_label: str
    elapsed_s: float


class AgentEvalOutput(BaseModel):
    """Top-level eval report shape, parallel to `eval.run_eval.EvalOutput`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    timestamp: str
    base_url: str
    model_label: str
    session_count: int
    sessions: list[SessionRecord]
    aggregates: dict[str, Any]
