"""Replay provider: returns the model replies of a recorded run, in order."""

from __future__ import annotations

import asyncio
from typing import Any

from ..cassettes import Cassette, Recorder
from ..config import settings
from ..llm import LLMError
from .base import LLMProvider, ProviderInfo


class ReplayProvider(LLMProvider):
    name = "replay"

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self._turn = 0
        self._vision = 0

    async def describe(self) -> ProviderInfo:
        return ProviderInfo(provider="replay", live=False,
                            note=f"recorded {self.cassette.recorded_at} with {self.cassette.llm or 'unknown model'}")

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int | None = None,
                   temperature: float | None = None) -> str:
        if self._turn >= len(self.cassette.llm_turns):
            raise LLMError("recording has no more model turns")
        turn = self.cassette.llm_turns[self._turn]
        self._turn += 1
        await asyncio.sleep(min(float(turn.get("latency_s", 0)) * settings.replay_speed, 2.5))
        return str(turn["text"])

    async def vision_check(self, claim: str, quote: str, image_b64: str, mime: str) -> dict[str, Any] | None:
        if self._vision >= len(self.cassette.vision):
            return None
        out = self.cassette.vision[self._vision]
        self._vision += 1
        return out


class RecordingProvider(LLMProvider):
    """Wraps a live provider and records every reply into a cassette."""

    def __init__(self, inner: LLMProvider, recorder: Recorder) -> None:
        self.inner, self.recorder = inner, recorder
        self.name = inner.name

    async def describe(self) -> ProviderInfo:
        return await self.inner.describe()

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int | None = None,
                   temperature: float | None = None) -> str:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        text = await self.inner.chat(messages, max_tokens=max_tokens, temperature=temperature)
        self.recorder.llm_turn(text, loop.time() - t0)
        return text

    async def vision_check(self, claim: str, quote: str, image_b64: str, mime: str) -> dict[str, Any] | None:
        out = await self.inner.vision_check(claim, quote, image_b64, mime)
        self.recorder.vision(out)
        return out

    async def ping(self) -> tuple[bool, str]:
        return await self.inner.ping()
