"""Unit tests for HableYaToolHandler (log_turn parser + TTS-strip processor)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from hable_ya.pipeline.processors.tool_handler import HableYaToolHandler
from hable_ya.runtime.observations import TurnObservationSink


async def _drive(
    handler: HableYaToolHandler, frames: list[Frame]
) -> list[Frame]:
    """Feed frames through the handler; capture everything pushed downstream.

    Bypasses FrameProcessor's pipeline-lifecycle machinery (started flags,
    observers, event handlers) by monkey-patching push_frame on the instance.
    """
    emitted: list[Frame] = []

    async def capture(
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        emitted.append(frame)

    handler.push_frame = capture  # type: ignore[method-assign]
    handler._observer = None

    for f in frames:
        await handler.process_frame(f, FrameDirection.DOWNSTREAM)
    return emitted


def _texts(frames: list[Frame]) -> list[str]:
    return [f.text for f in frames if isinstance(f, LLMTextFrame)]


@pytest.fixture
def sink(tmp_path: Path) -> TurnObservationSink:
    return TurnObservationSink(tmp_path / "turns.jsonl", ring_size=10)


async def test_happy_path_function_call_style(sink: TurnObservationSink) -> None:
    handler = HableYaToolHandler(sink, session_id="s1")
    tool_call = (
        'log_turn({"learner_utterance": "Yo es Juan.", '
        '"errors": [{"type": "ser_estar", "produced": "es", "target": "soy"}], '
        '"fluency_signal": "moderate", "L1_used": false})'
    )
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hola Juan. "),
        LLMTextFrame(tool_call),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    recent = sink.recent()
    assert len(recent) == 1
    obs = recent[0]
    assert obs.session_id == "s1"
    assert obs.learner_utterance == "Yo es Juan."
    assert obs.fluency_signal == "moderate"
    assert obs.L1_used is False
    assert obs.errors == [{"type": "ser_estar", "produced": "es", "target": "soy"}]
    assert sink.missing == 0

    cleaned = "".join(_texts(emitted))
    assert "log_turn(" not in cleaned
    assert ")" not in cleaned
    assert cleaned.strip() == "Hola Juan."
    # One Start, one End, one cleaned LLMTextFrame.
    assert sum(isinstance(f, LLMFullResponseStartFrame) for f in emitted) == 1
    assert sum(isinstance(f, LLMFullResponseEndFrame) for f in emitted) == 1
    assert sum(isinstance(f, LLMTextFrame) for f in emitted) == 1


async def test_happy_path_legacy_tool_call(sink: TurnObservationSink) -> None:
    handler = HableYaToolHandler(sink, session_id="s2")
    legacy = (
        '[TOOL_CALL: log_turn]{"learner_utterance": "Hola.", '
        '"errors": [], "fluency_signal": "strong", "L1_used": false}'
    )
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("¡Qué bien! "),
        LLMTextFrame(legacy),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    recent = sink.recent()
    assert len(recent) == 1
    assert recent[0].fluency_signal == "strong"
    assert sink.missing == 0

    cleaned = "".join(_texts(emitted))
    assert "TOOL_CALL" not in cleaned
    assert "¡Qué bien!" in cleaned


async def test_no_tool_call_increments_missing(sink: TurnObservationSink) -> None:
    handler = HableYaToolHandler(sink, session_id="s3")
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hola, "),
        LLMTextFrame("¿cómo estás?"),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    assert sink.recent() == []
    assert sink.missing == 1
    assert "".join(_texts(emitted)) == "Hola, ¿cómo estás?"


async def test_malformed_errors_dropped(sink: TurnObservationSink) -> None:
    handler = HableYaToolHandler(sink, session_id="s4")
    bad = (
        'log_turn({"learner_utterance": "Hola.", '
        '"errors": "not-a-list", '
        '"fluency_signal": "moderate", "L1_used": false})'
    )
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hola. "),
        LLMTextFrame(bad),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    assert sink.recent() == []
    assert sink.missing == 1
    cleaned = "".join(_texts(emitted))
    assert cleaned.strip() == "Hola."


async def test_invalid_fluency_signal_dropped(sink: TurnObservationSink) -> None:
    handler = HableYaToolHandler(sink, session_id="s5")
    bad = (
        'log_turn({"learner_utterance": "Hola.", '
        '"errors": [], "fluency_signal": "low", "L1_used": false})'
    )
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hola. "),
        LLMTextFrame(bad),
        LLMFullResponseEndFrame(),
    ]
    await _drive(handler, frames)

    assert sink.recent() == []
    assert sink.missing == 1


async def test_empty_response_emits_no_text(sink: TurnObservationSink) -> None:
    handler = HableYaToolHandler(sink, session_id="s6")
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame(
            'log_turn({"learner_utterance": "Hola.", "errors": [], '
            '"fluency_signal": "moderate", "L1_used": false})'
        ),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    assert sum(isinstance(f, LLMTextFrame) for f in emitted) == 0
    assert len(sink.recent()) == 1
    assert sink.missing == 0


async def test_non_llm_frames_pass_through(sink: TurnObservationSink) -> None:
    handler = HableYaToolHandler(sink, session_id="s7")

    class OtherFrame(Frame):
        pass

    other = OtherFrame()
    emitted = await _drive(handler, [other])

    assert emitted == [other]
    assert sink.missing == 0


async def test_unclosed_tool_call_counts_as_missing(
    sink: TurnObservationSink,
) -> None:
    """Unclosed JSON payload → parser finds no call → counted as missing.

    The malformed text still passes through (documented degradation path;
    a stricter parser is out of scope for this slice).
    """
    handler = HableYaToolHandler(sink, session_id="s8")
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Texto. [TOOL_CALL: log_turn]{broken"),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    assert sink.missing == 1
    cleaned = "".join(_texts(emitted))
    assert "Texto." in cleaned
