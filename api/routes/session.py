"""Voice session WebSocket endpoint.

One WS connection → one Pipecat PipelineTask. The shared services (STT/LLM/
TTS) and the app-wide observation sink come from `app.state` (loaded in
lifespan); per-session state (transport, LLM context, aggregators, custom
processors) is built fresh inside the handler.

Connection is refused with code 1013 ("try again later") if the app is still
warming up, so clients don't wait indefinitely on a dead pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, WebSocket
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.pipeline.base_task import PipelineTaskParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from hable_ya.pipeline.prompts.builder import build_system_prompt
from hable_ya.pipeline.runner import build_pipeline_task, default_learner
from hable_ya.pipeline.serializer import RawPCMSerializer
from hable_ya.tools.schema import HABLE_YA_TOOLS

logger = logging.getLogger("hable_ya.api.session")
router = APIRouter()


@router.websocket("/ws/session")
async def session_ws(websocket: WebSocket) -> None:
    app = websocket.app
    if not getattr(app.state, "ready", False):
        await websocket.close(code=1013, reason="warming up")
        return

    await websocket.accept()
    session_id = uuid.uuid4().hex[:12]
    logger.info("session %s: client connected", session_id)

    settings = app.state.settings
    services = app.state.services
    sink = app.state.observation_sink

    learner = default_learner(settings)
    context = LLMContext(
        messages=[
            {"role": "system", "content": build_system_prompt(learner)}
        ],
        tools=ToolsSchema(
            standard_tools=[],
            custom_tools={AdapterType.SHIM: HABLE_YA_TOOLS},
        ),
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

    task = build_pipeline_task(
        services,
        transport,
        context,
        settings,
        sink=sink,
        session_id=session_id,
    )

    try:
        await task.run(params=PipelineTaskParams(loop=asyncio.get_event_loop()))
    except Exception:
        logger.exception("session %s: pipeline error", session_id)
    finally:
        logger.info("session %s: client disconnected", session_id)
