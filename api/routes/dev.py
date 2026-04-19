"""Development-only observation inspection endpoint.

Mounted only when ``settings.dev_endpoints_enabled`` is true. Returns the most
recent :class:`TurnObservation` entries held in memory by the app's sink, plus
the running ``missing`` counter so the project owner can watch the fine-tuned
Gemma's ~80% log_turn emission rate in real time without tailing JSONL.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/dev/observations")
async def get_observations(
    request: Request, n: int = Query(100, ge=1, le=1000)
) -> dict[str, Any]:
    sink = request.app.state.observation_sink
    return {
        "missing": sink.missing,
        "observations": [asdict(obs) for obs in sink.recent(n)],
    }
