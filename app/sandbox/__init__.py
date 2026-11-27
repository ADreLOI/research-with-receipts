"""Sandbox backends and factory."""

from __future__ import annotations

from ..config import settings
from .base import Sandbox, SandboxInfo, ToolResult
from .docker_sandbox import DockerSandbox, docker_available, image_present
from .mock_sandbox import MockSandbox

__all__ = ["Sandbox", "SandboxInfo", "ToolResult", "make_sandbox", "resolve_backend"]

_resolved: str | None = None


async def resolve_backend() -> str:
    """Decide once per process which backend to use."""
    global _resolved
    if _resolved:
        return _resolved
    choice = settings.sandbox_backend
    if choice == "auto":
        choice = "docker" if (await docker_available() and await image_present(settings.sandbox_image)) else "mock"
    _resolved = choice
    return choice


async def make_sandbox() -> Sandbox:
    backend = await resolve_backend()
    if backend == "docker":
        return DockerSandbox()
    return MockSandbox()
