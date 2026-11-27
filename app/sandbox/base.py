"""Sandbox abstraction.

The control plane (this FastAPI app) never browses or executes model
written code itself. It asks a Sandbox to do it. A Sandbox owns one
isolated environment per research run and is destroyed when the run ends.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    tool: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_s: float = 0.0
    # Set when the sandbox itself had to step in (timeout, OOM, kill).
    contained: str = ""


@dataclass
class SandboxInfo:
    backend: str
    isolated: bool
    container_id: str = ""
    image: str = ""
    runtime: str = ""
    limits: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "isolated": self.isolated,
            "container_id": self.container_id,
            "image": self.image,
            "runtime": self.runtime,
            "limits": self.limits,
            "notes": self.notes,
        }


class Sandbox(abc.ABC):
    """One isolated environment, scoped to a single run."""

    name: str = "base"

    @abc.abstractmethod
    async def start(self, run_id: str) -> SandboxInfo: ...

    @abc.abstractmethod
    async def search(self, query: str, max_results: int = 6) -> ToolResult: ...

    @abc.abstractmethod
    async def fetch(self, url: str) -> ToolResult: ...

    @abc.abstractmethod
    async def screenshot(self, url: str, highlight: str = "") -> ToolResult: ...

    @abc.abstractmethod
    async def run_python(self, code: str) -> ToolResult: ...

    @abc.abstractmethod
    async def destroy(self) -> None: ...

    async def __aenter__(self) -> "Sandbox":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.destroy()
