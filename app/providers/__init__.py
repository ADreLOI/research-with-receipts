"""Provider factory and live-model status."""

from __future__ import annotations

from ..config import settings
from .base import LLMProvider, ProviderInfo
from .mock import MockProvider
from .replay import RecordingProvider, ReplayProvider
from .vultr import VultrProvider

__all__ = ["LLMProvider", "ProviderInfo", "MockProvider", "ReplayProvider", "RecordingProvider", "VultrProvider",
           "make_live_provider", "live_status"]


def make_live_provider() -> LLMProvider:
    """Provider for live runs: Vultr when configured (LLM_PROVIDER / VULTR_INFERENCE_API_KEY), else the mock."""
    if settings.llm_enabled:
        return VultrProvider()
    if settings.llm_provider == "vultr":
        return MockProvider(note="LLM_PROVIDER=vultr but VULTR_INFERENCE_API_KEY is empty")
    return MockProvider()


async def live_status() -> dict:
    """What a live run would use right now, and whether it is reachable."""
    if not settings.llm_enabled:
        reason = "LLM_PROVIDER=mock" if settings.llm_provider == "mock" else "VULTR_INFERENCE_API_KEY not set"
        return {"provider": "mock", "ok": True, "live": False, "detail": f"mock policy ({reason})"}
    ok, detail = await VultrProvider().ping()
    return {"provider": "vultr", "ok": ok, "live": True, "detail": detail}
