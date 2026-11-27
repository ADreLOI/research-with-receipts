"""Provider factory."""

from __future__ import annotations

from ..config import settings
from .base import LLMProvider, ProviderInfo
from .mock import MockProvider
from .replay import RecordingProvider, ReplayProvider
from .vultr import VultrProvider

__all__ = ["LLMProvider", "ProviderInfo", "MockProvider", "ReplayProvider", "RecordingProvider", "VultrProvider",
           "make_live_provider"]


def make_live_provider() -> LLMProvider:
    """The provider used for live runs: Vultr when a key is configured, else the mock policy."""
    if settings.llm_enabled:
        return VultrProvider()
    return MockProvider()
