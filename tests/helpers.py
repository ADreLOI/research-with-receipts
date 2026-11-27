"""Shared test doubles."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from app.config import settings
from app.providers.base import LLMProvider, ProviderInfo
from app.sandbox.base import ToolResult
from app.sandbox.mock_sandbox import MockSandbox


class ScriptedProvider(LLMProvider):
    """Returns a fixed list of replies and remembers every prompt it saw."""

    name = "scripted"

    def __init__(self, replies: list[Any]) -> None:
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.seen: list[list[dict[str, Any]]] = []

    async def describe(self) -> ProviderInfo:
        return ProviderInfo(provider="scripted", live=False)

    async def chat(self, messages, *, max_tokens=None, temperature=None) -> str:
        self.seen.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("scripted provider ran out of replies")
        return self.replies.pop(0)


class CountingSandbox(MockSandbox):
    """Mock sandbox that records calls and can inject failures."""

    def __init__(self, fail_python: list[ToolResult] | None = None, explode_on: str = "") -> None:
        super().__init__()
        self.calls: list[tuple[str, dict]] = []
        self.destroyed = False
        self.fail_python = list(fail_python or [])
        self.explode_on = explode_on

    async def search(self, query, max_results=6):
        self.calls.append(("search", {"query": query}))
        return await super().search(query, max_results)

    async def screenshot(self, url, highlight=""):
        self.calls.append(("screenshot", {"url": url, "highlight": highlight}))
        if self.explode_on == "screenshot":
            raise RuntimeError("sandbox crashed")
        return await super().screenshot(url, highlight)

    async def fetch(self, url):
        self.calls.append(("fetch", {"url": url}))
        return await super().fetch(url)

    async def run_python(self, code):
        self.calls.append(("python", {"code": code}))
        if self.fail_python:
            return self.fail_python.pop(0)
        return await super().run_python(code)

    async def destroy(self):
        self.destroyed = True
        await super().destroy()


@contextmanager
def override(**values: Any):
    """Temporarily change fields on the frozen settings object."""
    old = {k: getattr(settings, k) for k in values}
    for k, v in values.items():
        object.__setattr__(settings, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            object.__setattr__(settings, k, v)


def act(action: str, thought: str = "t", **args: Any) -> dict[str, Any]:
    return {"thought": thought, "action": action, "args": args}


GVISOR = "https://mock.local/gvisor"
GVISOR_QUOTE = "gVisor includes an OCI runtime called runsc that integrates with Docker and Kubernetes."
