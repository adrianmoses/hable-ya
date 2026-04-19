"""Pipecat pipeline composition for a single voice session.

One WS connection → one `PipelineTask` built by `build_pipeline_task()`.
Services (STT / LLM / TTS) are injected from the module-level shared pool in
`hable_ya.pipeline.services` so each connection only owns the per-session
bits: the transport, the LLM context, VAD/Smart-Turn analyzers, and the two
custom processors.
"""
from __future__ import annotations

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
    LocalSmartTurnAnalyzerV3,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from hable_ya.config import Settings
from hable_ya.pipeline.processors.tool_handler import HableYaToolHandler
from hable_ya.pipeline.processors.turn_observer import HableYaTurnObserver
from hable_ya.pipeline.services import Services

DEFAULT_LEARNER: dict[str, object] = {"band": "A2", "learner_id": "placeholder"}


def build_pipeline(
    services: Services,
    transport: FastAPIWebsocketTransport,
    context: LLMContext,
    settings: Settings,
) -> Pipeline:
    """Assemble the voice pipeline.

    Order is load-bearing: the tool handler sits immediately before TTS so
    `[TOOL_CALL: ...]{...}` never reaches speech synthesis.
    """
    smart_turn = LocalSmartTurnAnalyzerV3(
        params=SmartTurnParams(stop_secs=settings.smart_turn_stop_secs)
    )
    user_params = LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(
            stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=smart_turn)]
        ),
        vad_analyzer=SileroVADAnalyzer(
            sample_rate=settings.audio_sample_rate,
            params=VADParams(stop_secs=settings.vad_stop_secs),
        ),
    )
    aggregators = LLMContextAggregatorPair(context, user_params=user_params)

    turn_observer = HableYaTurnObserver()
    tool_handler = HableYaToolHandler()

    return Pipeline(
        [
            transport.input(),
            services.stt,
            turn_observer,
            aggregators.user(),
            services.llm,
            tool_handler,
            services.tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )


def build_pipeline_task(
    services: Services,
    transport: FastAPIWebsocketTransport,
    context: LLMContext,
    settings: Settings,
) -> PipelineTask:
    pipeline = build_pipeline(services, transport, context, settings)
    return PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            audio_in_sample_rate=settings.audio_sample_rate,
            audio_out_sample_rate=settings.audio_sample_rate,
        ),
        idle_timeout_secs=None,
    )
