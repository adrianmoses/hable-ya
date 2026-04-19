"""Strip tool-call syntax from the LLM text stream before it reaches TTS.

Buffers every `LLMTextFrame` between `LLMFullResponseStartFrame` and
`LLMFullResponseEndFrame`, then emits a single cleaned `LLMTextFrame` with
`[TOOL_CALL: name]{...}` and `name({...})` forms removed.
"""
from __future__ import annotations

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from eval.scoring.turn import strip_tool_calls


class HableYaToolHandler(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self._buffer: list[str] = []
        self._buffering: bool = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = []
            self._buffering = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._buffering:
                cleaned = strip_tool_calls("".join(self._buffer))
                if cleaned:
                    await self.push_frame(LLMTextFrame(cleaned), direction)
                self._buffering = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame) and self._buffering:
            self._buffer.append(frame.text)
            return

        await self.push_frame(frame, direction)
