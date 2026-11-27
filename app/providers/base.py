"""LLM provider interface.

The agent loop only talks to an LLMProvider. Implementations:
  * VultrProvider  - Vultr Serverless Inference (the real thing)
  * MockProvider   - deterministic offline policy for dev and tests
  * ReplayProvider - replays a recorded run (demo safety net)
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ProviderInfo:
    provider: str
    model: str = ""
    vision_model: str = ""
    live: bool = False
    note: str = ""

    @property
    def label(self) -> str:
        if self.provider == "vultr":
            return f"vultr:{self.model}" + (f" (vision: {self.vision_model})" if self.vision_model else "")
        return self.provider + (f" ({self.note})" if self.note else "")

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "label": self.label}


class LLMProvider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    async def describe(self) -> ProviderInfo: ...

    @abc.abstractmethod
    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int | None = None,
                   temperature: float | None = None) -> str:
        """Return the assistant's reply text for an OpenAI-style message list."""

    async def vision_check(self, claim: str, quote: str, image_b64: str, mime: str) -> dict[str, Any] | None:
        """Optionally ask a vision model whether a screenshot supports a claim."""
        return None

    async def ping(self) -> tuple[bool, str]:
        """Cheap reachability check used by /api/health and demo auto mode."""
        return True, "ok"
