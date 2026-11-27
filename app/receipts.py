"""Receipts: immutable evidence captured inside the sandbox.

A receipt is created for every page the agent reads and every piece of
code it runs. Claims in the final answer must cite receipt ids, and each
citation is checked mechanically against the captured text.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Receipt:
    id: str
    kind: str  # "page" | "code"
    tool: str
    step: int
    captured_at: str
    sandbox: str
    url: str = ""
    title: str = ""
    text: str = ""
    code: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    image_file: str = ""
    image_mime: str = ""
    sha256: str = ""
    highlight: str = ""
    highlight_found: bool = False
    blocked_requests: list[str] = field(default_factory=list)
    evidence_for: list[int] = field(default_factory=list)

    def public(self, include_text: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_text:
            d["text"] = self.text[:1200]
        d["has_image"] = bool(self.image_file)
        return d


class ReceiptStore:
    """Per-run receipt registry, with images persisted next to the run."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir / "receipts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items: dict[str, Receipt] = {}

    def next_id(self) -> str:
        return f"R{len(self.items) + 1}"

    def add(self, receipt: Receipt, image_b64: str = "", image_mime: str = "") -> Receipt:
        h = hashlib.sha256()
        h.update((receipt.url + "\n" + receipt.text + "\n" + receipt.code + "\n" + receipt.stdout).encode())
        if image_b64:
            raw = base64.b64decode(image_b64)
            h.update(raw)
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/svg+xml": "svg"}.get(image_mime, "bin")
            name = f"{receipt.id}.{ext}"
            (self.dir / name).write_bytes(raw)
            receipt.image_file, receipt.image_mime = name, image_mime
        receipt.sha256 = h.hexdigest()
        self.items[receipt.id] = receipt
        return receipt

    def get(self, rid: str) -> Receipt | None:
        return self.items.get(rid.strip().upper().strip("[]"))

    def find_page(self, url: str) -> Receipt | None:
        for r in self.items.values():
            if r.kind == "page" and r.url.rstrip("/") == url.rstrip("/"):
                return r
        return None


# ------------------------------------------------------------ verification

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " "})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).lower()
    return re.sub(r"\s+", " ", text).strip()


def match_quote(quote: str, text: str) -> tuple[float, str]:
    """Return (score 0..1, best matching span) of a quote inside text."""
    q, t = normalize(quote), normalize(text)
    if not q or not t:
        return 0.0, ""
    if q in t:
        return 1.0, quote
    sm = difflib.SequenceMatcher(None, t, q, autojunk=False)
    blocks = sm.get_matching_blocks()
    matched = sum(b.size for b in blocks if b.size >= 4)
    longest = max((b for b in blocks), key=lambda b: b.size)
    span = t[longest.a:longest.a + longest.size]
    return round(min(1.0, matched / len(q)) * 0.5 + (longest.size / len(q)) * 0.5, 3), span


def verify_claim(claim: dict[str, Any], store: ReceiptStore) -> dict[str, Any]:
    """Mechanically check a claim against the receipts it cites."""
    quote = str(claim.get("quote", ""))
    checks = []
    best = 0.0
    for rid in claim.get("receipt_ids", []):
        r = store.get(str(rid))
        if r is None:
            checks.append({"receipt_id": rid, "exists": False, "score": 0.0})
            continue
        haystack = r.text if r.kind == "page" else (r.stdout + "\n" + r.code)
        score, span = match_quote(quote, haystack) if quote else (0.0, "")
        best = max(best, score)
        checks.append({"receipt_id": r.id, "exists": True, "kind": r.kind, "score": score, "span": span[:200],
                       "highlight_found": r.highlight_found if r.kind == "page" else None})
    visual = any(c.get("highlight_found") or c.get("kind") == "code" for c in checks if c.get("score", 0) >= 0.9)
    if best >= 0.9 and visual:
        status = "verified"
    elif best >= 0.6:
        status = "partial"
    else:
        status = "unverified"
    return {"status": status, "quote_score": best, "checks": checks}
