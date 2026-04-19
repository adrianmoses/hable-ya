"""Lifecycle tests for /health and /ws/session warmup gating.

Uses TestClient with lifespan. Services and warmup are patched so the test
doesn't need CUDA, model downloads, or a live llama.cpp endpoint.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hable_ya.config import Settings


def _fake_load_services(_settings: Settings) -> Any:
    return MagicMock(name="fake-services")


async def _noop_warmup(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.fixture
def ready_app() -> Iterator[TestClient]:
    with (
        patch("api.main.load_services", _fake_load_services),
        patch("api.main.warmup_llm", _noop_warmup),
    ):
        from api.main import app

        with TestClient(app) as client:
            yield client


def test_health_returns_200_after_warmup(ready_app: TestClient) -> None:
    response = ready_app.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "llm_backend" in body


def test_health_returns_503_before_warmup_completes() -> None:
    """Simulate mid-warmup state by overriding the ready flag.

    TestClient's lifespan runs to completion before yielding. To observe the
    503 path we can't keep lifespan suspended mid-warmup, so instead we drive
    the handler directly against app.state.
    """
    with (
        patch("api.main.load_services", _fake_load_services),
        patch("api.main.warmup_llm", _noop_warmup),
    ):
        from api.main import app

        with TestClient(app) as client:
            # Force back to warming-up state, as if a caller arrived before
            # lifespan finished.
            app.state.ready = False
            response = client.get("/health")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "warming_up"


def test_ws_session_refused_while_warming_up() -> None:
    with (
        patch("api.main.load_services", _fake_load_services),
        patch("api.main.warmup_llm", _noop_warmup),
    ):
        from api.main import app

        with TestClient(app) as client:
            app.state.ready = False
            # Starlette's TestClient raises WebSocketDisconnect when the server
            # closes before/instead of accepting.
            from starlette.websockets import WebSocketDisconnect

            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect("/ws/session"):
                    pass
            assert excinfo.value.code == 1013
