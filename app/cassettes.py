"""Record and replay of complete runs ("cassettes").

Every live run records what the model said and what the sandbox returned.
A recorded run can be promoted into demo/cassettes/ and replayed later,
step by step, through the same agent loop, receipts and verification.
Replays are always labelled as replays in the API and the UI.

Layout of a cassette directory:
  cassette.json   question, metadata, llm_turns, vision, tool_calls
  tc<N>.<ext>     screenshots referenced by tool_calls[*].data.image_file
"""

from __future__ import annotations

import base64
import copy
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ROOT, settings

VERSION = 1
SHIPPED_DIR = ROOT / "demo" / "cassettes"
_URL_BASE = re.compile(r"https?://[^/\s]+(?=/demo/)")


def normalize_question(q: str) -> str:
    """Normalize so cosmetic differences (case, punctuation, demo host) still match."""
    q = _URL_BASE.sub("{base}", q.strip().lower())
    q = re.sub(r"[^\w{}/:.]+", " ", q)
    return re.sub(r"\s+", " ", q).strip(" .")


def slugify(q: str) -> str:
    words = re.findall(r"[a-z0-9]+", normalize_question(q).replace("{base}", ""))
    return "-".join(words[:8]) or "run"


@dataclass
class Cassette:
    question: str
    recorded_at: str = ""
    llm: str = ""
    sandbox: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    aliases: list[str] = field(default_factory=list)
    llm_turns: list[dict[str, Any]] = field(default_factory=list)
    vision: list[dict[str, Any] | None] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    dir: Path | None = None

    @property
    def slug(self) -> str:
        return self.dir.name if self.dir else slugify(self.question)

    def matches(self, question: str) -> bool:
        n = normalize_question(question)
        return n == normalize_question(self.question) or n in {normalize_question(a) for a in self.aliases}

    def image_b64(self, name: str) -> str:
        assert self.dir is not None
        path = (self.dir / name).resolve()
        if self.dir.resolve() not in path.parents:
            raise ValueError("image path escapes cassette dir")
        return base64.b64encode(path.read_bytes()).decode()

    def summary(self) -> dict[str, Any]:
        return {"slug": self.slug, "question": self.question, "recorded_at": self.recorded_at, "llm": self.llm,
                "sandbox_backend": self.sandbox.get("backend", ""), "note": self.note,
                "steps": len(self.llm_turns), "tool_calls": len(self.tool_calls)}

    # ------------------------------------------------------------ io

    @classmethod
    def load(cls, directory: Path) -> "Cassette":
        data = json.loads((directory / "cassette.json").read_text())
        if data.get("version") != VERSION:
            raise ValueError(f"unsupported cassette version in {directory}")
        return cls(question=data["question"], recorded_at=data.get("recorded_at", ""), llm=data.get("llm", ""),
                   sandbox=data.get("sandbox", {}), note=data.get("note", ""), aliases=data.get("aliases", []),
                   llm_turns=data.get("llm_turns", []), vision=data.get("vision", []),
                   tool_calls=data.get("tool_calls", []), dir=directory)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "question": self.question, "recorded_at": self.recorded_at, "llm": self.llm,
                   "sandbox": self.sandbox, "note": self.note, "aliases": self.aliases,
                   "llm_turns": self.llm_turns, "vision": self.vision, "tool_calls": self.tool_calls}
        (directory / "cassette.json").write_text(json.dumps(payload, indent=1))
        self.dir = directory
        return directory


class Recorder:
    """Collects one run's model replies and sandbox results."""

    def __init__(self, question: str) -> None:
        self.cassette = Cassette(question=question, recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._images: dict[str, bytes] = {}

    def llm_turn(self, text: str, latency_s: float) -> None:
        self.cassette.llm_turns.append({"text": text, "latency_s": round(latency_s, 2)})

    def vision(self, result: dict[str, Any] | None) -> None:
        self.cassette.vision.append(result)

    def tool_call(self, tool: str, args: dict[str, Any], ok: bool, data: dict[str, Any], error: str,
                  duration_s: float, contained: str) -> None:
        data = copy.deepcopy(data)  # never mutate the live result the agent is about to use
        if data.get("image_b64"):
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/svg+xml": "svg"}.get(data.get("image_mime", ""), "bin")
            name = f"tc{len(self.cassette.tool_calls) + 1}.{ext}"
            self._images[name] = base64.b64decode(data.pop("image_b64"))
            data["image_file"] = name
        files = data.get("files") or []
        for i, f in enumerate(files):
            name = f"tc{len(self.cassette.tool_calls) + 1}-file{i}.png"
            self._images[name] = base64.b64decode(f.pop("b64"))
            f["file"] = name
        self.cassette.tool_calls.append({"tool": tool, "args": args, "ok": ok, "data": data, "error": error,
                                         "duration_s": round(duration_s, 2), "contained": contained})

    def save(self, directory: Path, llm: str, sandbox: dict[str, Any]) -> Path:
        self.cassette.llm, self.cassette.sandbox = llm, sandbox
        self.cassette.save(directory)
        for name, raw in self._images.items():
            (directory / name).write_bytes(raw)
        return directory


class CassetteLibrary:
    """Shipped cassettes (demo/cassettes) plus promoted ones (DATA_DIR/cassettes)."""

    def __init__(self, dirs: list[Path] | None = None) -> None:
        self.dirs = dirs or [settings.data_dir / "cassettes", SHIPPED_DIR]

    def all(self) -> list[Cassette]:
        out: list[Cassette] = []
        seen: set[str] = set()
        for base in self.dirs:
            if not base.is_dir():
                continue
            for path in sorted(base.glob("*/cassette.json")):
                try:
                    c = Cassette.load(path.parent)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    continue
                key = normalize_question(c.question)
                if key not in seen:  # promoted recordings shadow shipped ones
                    seen.add(key)
                    out.append(c)
        return out

    def find(self, question: str) -> Cassette | None:
        return next((c for c in self.all() if c.matches(question)), None)


library = CassetteLibrary()
