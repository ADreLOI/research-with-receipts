"""Replay and recording wrappers at the sandbox boundary."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from ..cassettes import Cassette, Recorder
from ..config import settings
from .base import Sandbox, SandboxInfo, ToolResult

_KEY_ARG = {"search": "query", "fetch": "url", "screenshot": "url", "python": "code"}


class ReplaySandbox(Sandbox):
    """Serves recorded tool results. Nothing is executed and nothing is fetched."""

    name = "replay"

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self.used: set[int] = set()

    async def start(self, run_id: str) -> SandboxInfo:
        orig = self.cassette.sandbox or {}
        return SandboxInfo(
            backend="replay", isolated=False, container_id=f"replay-{self.cassette.slug[:16]}",
            image=orig.get("image", ""), runtime=f"recording of {orig.get('backend', '?')}:{orig.get('container_id', '?')}",
            limits=orig.get("limits", {}),
            notes=(f"REPLAY of a run recorded {self.cassette.recorded_at}. Results come from the recording; "
                   "receipts, hashes and verification are recomputed live."))

    def _take(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        key = _KEY_ARG.get(tool, "")
        want = str(args.get(key, ""))
        exact = [i for i, c in enumerate(self.cassette.tool_calls)
                 if i not in self.used and c["tool"] == tool and str(c["args"].get(key, "")) == want
                 and c["args"].get("highlight", "") == args.get("highlight", "")]
        loose = [i for i, c in enumerate(self.cassette.tool_calls)
                 if i not in self.used and c["tool"] == tool and str(c["args"].get(key, "")) == want]
        pick = (exact or loose or [None])[0]
        if pick is None:
            return None
        self.used.add(pick)
        return self.cassette.tool_calls[pick]

    async def _replay(self, tool: str, args: dict[str, Any]) -> ToolResult:
        call = self._take(tool, args)
        if call is None:
            return ToolResult(False, tool, error=f"replay: this {tool} call is not in the recording")
        await asyncio.sleep(min(float(call.get("duration_s", 0)) * settings.replay_speed, 3.0))
        data = copy.deepcopy(call.get("data", {}))
        if data.get("image_file"):
            data["image_b64"] = self.cassette.image_b64(data.pop("image_file"))
        for f in data.get("files") or []:
            if f.get("file"):
                f["b64"] = self.cassette.image_b64(f.pop("file"))
        return ToolResult(bool(call.get("ok")), tool, data=data, error=call.get("error", ""),
                          duration_s=float(call.get("duration_s", 0)), contained=call.get("contained", ""))

    async def search(self, query: str, max_results: int = 6) -> ToolResult:
        return await self._replay("search", {"query": query})

    async def fetch(self, url: str) -> ToolResult:
        return await self._replay("fetch", {"url": url})

    async def screenshot(self, url: str, highlight: str = "") -> ToolResult:
        return await self._replay("screenshot", {"url": url, "highlight": highlight})

    async def run_python(self, code: str) -> ToolResult:
        return await self._replay("python", {"code": code})

    async def destroy(self) -> None:
        return None


class RecordingSandbox(Sandbox):
    """Wraps a real sandbox and records every tool result."""

    def __init__(self, inner: Sandbox, recorder: Recorder) -> None:
        self.inner, self.recorder = inner, recorder
        self.name = inner.name

    async def start(self, run_id: str) -> SandboxInfo:
        return await self.inner.start(run_id)

    def _rec(self, tool: str, args: dict[str, Any], res: ToolResult) -> ToolResult:
        self.recorder.tool_call(tool, args, res.ok, res.data, res.error, res.duration_s, res.contained)
        return res

    async def search(self, query: str, max_results: int = 6) -> ToolResult:
        return self._rec("search", {"query": query}, await self.inner.search(query, max_results))

    async def fetch(self, url: str) -> ToolResult:
        return self._rec("fetch", {"url": url}, await self.inner.fetch(url))

    async def screenshot(self, url: str, highlight: str = "") -> ToolResult:
        return self._rec("screenshot", {"url": url, "highlight": highlight},
                         await self.inner.screenshot(url, highlight))

    async def run_python(self, code: str) -> ToolResult:
        return self._rec("python", {"code": code}, await self.inner.run_python(code))

    async def destroy(self) -> None:
        await self.inner.destroy()
