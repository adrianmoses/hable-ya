"""Voice session WebSocket endpoint.

One WS connection → one Pipecat PipelineTask. The shared services (STT/LLM/
TTS) come from `app.state.services` (loaded in lifespan); per-session state
(transport, LLM context, aggregators, custom processors) is built fresh inside
the handler.

Connection is refused with code 1013 ("try again later") if the app is still
warming up, so clients don't wait indefinitely on a dead pipeline.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket
from pipecat.pipeline.base_task import PipelineTaskParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from hable_ya.pipeline.prompts.builder import build_system_prompt
from hable_ya.pipeline.runner import DEFAULT_LEARNER, build_pipeline_task
from hable_ya.pipeline.serializer import RawPCMSerializer

logger = logging.getLogger("hable_ya.api.session")
router = APIRouter()


@router.websocket("/ws/session")
async def session_ws(websocket: WebSocket) -> None:
    app = websocket.app
    if not getattr(app.state, "ready", False):
        await websocket.close(code=1013, reason="warming up")
        return

    await websocket.accept()
    logger.info("session: client connected")

    settings = app.state.settings
    services = app.state.services

    context = LLMContext(
        messages=[
            {"role": "system", "content": build_system_prompt(DEFAULT_LEARNER)}
        ]
    )
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            serializer=RawPCMSerializer(settings.audio_sample_rate),
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=settings.audio_sample_rate,
            audio_out_sample_rate=settings.audio_sample_rate,
            audio_in_channels=1,
            audio_out_channels=1,
        ),
    )

    task = build_pipeline_task(services, transport, context, settings)

    try:
        await task.run(params=PipelineTaskParams(loop=asyncio.get_event_loop()))
    except Exception:
        logger.exception("session: pipeline error")
    finally:
        logger.info("session: client disconnected")
