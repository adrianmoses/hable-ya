"""Unit tests for HableYaToolHandler (tool-call stripping processor)."""
from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from hable_ya.pipeline.processors.tool_handler import HableYaToolHandler


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

    # Also avoid the observer branch in super().process_frame.
    handler._observer = None

    for f in frames:
        await handler.process_frame(f, FrameDirection.DOWNSTREAM)
    return emitted


def _texts(frames: list[Frame]) -> list[str]:
    return [f.text for f in frames if isinstance(f, LLMTextFrame)]


@pytest.mark.asyncio
async def test_strips_tool_call_at_tail() -> None:
    handler = HableYaToolHandler()
    response = (
        '¡Qué bien! ¿Adónde fuiste?\n\n'
        '[TOOL_CALL: log_turn]{"learner_id": "x", "L1_used": false, '
        '"errors_observed": [], "vocab_produced": [], "fluency_signal": "strong"}'
    )
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("¡Qué bien! "),
        LLMTextFrame("¿Adónde fuiste?\n\n"),
        LLMTextFrame(
            '[TOOL_CALL: log_turn]{"learner_id": "x", "L1_used": false, '
            '"errors_observed": [], "vocab_produced": [], '
            '"fluency_signal": "strong"}'
        ),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    cleaned = "".join(_texts(emitted))
    assert "TOOL_CALL" not in cleaned
    assert "¡Qué bien!" in cleaned
    assert "¿Adónde fuiste?" in cleaned
    assert response  # sanity — original contained the tool call

    # Expect exactly one Start, one End, and one cleaned LLMTextFrame.
    assert sum(isinstance(f, LLMFullResponseStartFrame) for f in emitted) == 1
    assert sum(isinstance(f, LLMFullResponseEndFrame) for f in emitted) == 1
    assert sum(isinstance(f, LLMTextFrame) for f in emitted) == 1


@pytest.mark.asyncio
async def test_passes_through_when_no_tool_call() -> None:
    handler = HableYaToolHandler()
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hola, "),
        LLMTextFrame("¿cómo estás?"),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    cleaned = "".join(_texts(emitted))
    assert cleaned == "Hola, ¿cómo estás?"


@pytest.mark.asyncio
async def test_strips_function_call_style() -> None:
    """Gemma base model sometimes emits `name({...})` instead of the bracket form.

    The closing `)` must be consumed along with the payload — otherwise TTS
    synthesizes a lone paren at the end of the utterance.
    """
    handler = HableYaToolHandler()
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Entiendo. "),
        LLMTextFrame(
            'log_turn({"learner_id": "x", "L1_used": false, '
            '"errors_observed": [], "vocab_produced": [], '
            '"fluency_signal": "moderate"})'
        ),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    cleaned = "".join(_texts(emitted))
    assert "log_turn(" not in cleaned
    assert ")" not in cleaned
    assert cleaned.strip() == "Entiendo."


@pytest.mark.asyncio
async def test_malformed_tool_call_passes_through() -> None:
    """Unclosed JSON payload — parser returns no span, text passes through.

    Accepted v1 behavior per spec §Testing Approach. Spec 025 can tighten this.
    """
    handler = HableYaToolHandler()
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Texto. [TOOL_CALL: log_turn]{broken"),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    cleaned = "".join(_texts(emitted))
    assert "Texto." in cleaned
    # The malformed block survives — documented behavior.
    assert "TOOL_CALL" in cleaned


@pytest.mark.asyncio
async def test_non_llm_frames_pass_through_unchanged() -> None:
    """System frames between responses should not be buffered or dropped."""
    handler = HableYaToolHandler()

    class OtherFrame(Frame):
        pass

    other = OtherFrame()
    emitted = await _drive(handler, [other])

    assert emitted == [other]


@pytest.mark.asyncio
async def test_empty_response_does_not_emit_text_frame() -> None:
    """If the response is entirely a tool call, no TTS text is emitted."""
    handler = HableYaToolHandler()
    frames: list[Frame] = [
        LLMFullResponseStartFrame(),
        LLMTextFrame(
            '[TOOL_CALL: log_turn]{"learner_id": "x", "L1_used": false, '
            '"errors_observed": [], "vocab_produced": [], '
            '"fluency_signal": "low"}'
        ),
        LLMFullResponseEndFrame(),
    ]
    emitted = await _drive(handler, frames)

    assert sum(isinstance(f, LLMTextFrame) for f in emitted) == 0
    assert sum(isinstance(f, LLMFullResponseStartFrame) for f in emitted) == 1
    assert sum(isinstance(f, LLMFullResponseEndFrame) for f in emitted) == 1
