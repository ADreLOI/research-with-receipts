"""Run registry and event bus.

Each run has an append-only event log. The web UI subscribes to it over
Server-Sent Events, so the user sees every plan step, tool call, retry and
sandbox lifecycle event live.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings
from .receipts import ReceiptStore


@dataclass
class Run:
    id: str
    question: str
    created_at: float
    dir: Path
    status: str = "queued"  # queued | running | done | failed
    events: list[dict[str, Any]] = field(default_factory=list)
    answer: dict[str, Any] | None = None
    sandbox: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    llm: str = ""
    receipts: ReceiptStore = field(init=False)
    _subs: list[asyncio.Queue] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.receipts = ReceiptStore(self.dir)

    def emit(self, type_: str, **data: Any) -> dict[str, Any]:
        ev = {"seq": len(self.events), "t": round(time.time() - self.created_at, 2), "type": type_, **data}
        self.events.append(ev)
        for q in list(self._subs):
            q.put_nowait(ev)
        return ev

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "question": self.question, "status": self.status, "created_at": self.created_at,
                "n_receipts": len(self.receipts.items), "n_events": len(self.events)}

    def public(self) -> dict[str, Any]:
        return {**self.summary(), "answer": self.answer, "sandbox": self.sandbox, "error": self.error,
                "llm": self.llm, "events": self.events,
                "receipts": [r.public() for r in self.receipts.items.values()]}

    def save(self) -> None:
        data = self.public()
        data["receipts"] = [r.public(include_text=True) for r in self.receipts.items.values()]
        (self.dir / "run.json").write_text(json.dumps(data, indent=1))


class RunRegistry:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.root = settings.data_dir / "runs"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, question: str) -> Run:
        rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        run = Run(id=rid, question=question, created_at=time.time(), dir=self.root / rid)
        self.runs[rid] = run
        return run

    def get(self, rid: str) -> Run | None:
        return self.runs.get(rid)

    def load_saved(self) -> None:
        """Reload finished runs from disk so history survives restarts."""
        from .receipts import Receipt

        for path in sorted(self.root.glob("*/run.json"))[-50:]:
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            run = Run(id=data["id"], question=data["question"], created_at=data["created_at"], dir=path.parent,
                      status=data.get("status", "done"), events=data.get("events", []), answer=data.get("answer"),
                      sandbox=data.get("sandbox", {}), error=data.get("error", ""), llm=data.get("llm", ""))
            if run.status in {"queued", "running"}:
                run.status = "failed"
                run.error = run.error or "server restarted during run"
            for rd in data.get("receipts", []):
                rd.pop("has_image", None)
                run.receipts.items[rd["id"]] = Receipt(**rd)
            self.runs[run.id] = run

    def list(self) -> list[dict[str, Any]]:
        return [r.summary() for r in sorted(self.runs.values(), key=lambda r: -r.created_at)]


registry = RunRegistry()
