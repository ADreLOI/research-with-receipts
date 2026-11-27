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
    # Set when the quote was located in the page but is hidden from view
    # (tiny, transparent, off-screen...). The text is evidence, the image is not.
    highlight_hidden: str = ""
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
        raw = b""
        if image_b64:
            raw = base64.b64decode(image_b64)
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/svg+xml": "svg"}.get(image_mime, "bin")
            name = f"{receipt.id}.{ext}"
            (self.dir / name).write_bytes(raw)
            receipt.image_file, receipt.image_mime = name, image_mime
        receipt.sha256 = receipt_hash(receipt, raw)
        self.items[receipt.id] = receipt
        return receipt

    def get(self, rid: str) -> Receipt | None:
        return self.items.get(rid.strip().upper().strip("[]"))

    def find_page(self, url: str) -> Receipt | None:
        for r in self.items.values():
            if r.kind == "page" and r.url.rstrip("/") == url.rstrip("/"):
                return r
        return None

    def image_bytes(self, receipt: Receipt) -> bytes:
        return (self.dir / receipt.image_file).read_bytes() if receipt.image_file else b""

    def verify_hash(self, receipt: Receipt) -> dict[str, Any]:
        """Recompute a receipt's SHA-256 from what is stored now and compare."""
        raw = self.image_bytes(receipt)
        recomputed = receipt_hash(receipt, raw)
        return {"receipt_id": receipt.id, "stored": receipt.sha256, "recomputed": recomputed,
                "match": recomputed == receipt.sha256, "image_bytes": len(raw),
                "text_chars": len(receipt.text), "formula": HASH_FORMULA}


HASH_FORMULA = "sha256(utf8(url + '\\n' + text + '\\n' + code + '\\n' + stdout) + image_bytes)"


def receipt_hash(receipt: Receipt, image: bytes = b"") -> str:
    """Content hash of a receipt. The web UI recomputes the same formula in the browser."""
    h = hashlib.sha256()
    h.update((receipt.url + "\n" + receipt.text + "\n" + receipt.code + "\n" + receipt.stdout).encode())
    h.update(image)
    return h.hexdigest()

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


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def best_sentence(target: str, text: str) -> tuple[str, float]:
    """Sentence of `text` most similar to `target` (ratio 0..1), used to snap paraphrased quotes."""
    t = normalize(target)
    if not t:
        return "", 0.0
    best, best_ratio = "", 0.0
    for sent in _SENT_SPLIT.split(text or ""):
        sent = sent.strip()
        if len(sent) < 12:
            continue
        ratio = difflib.SequenceMatcher(None, t, normalize(sent), autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = sent, ratio
    return best, round(best_ratio, 3)


_WORD = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
_STOPWORDS = set("a an and are as at be by for from has have in is it its of on or that the their this to was were "
                 "which with".split())


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(normalize(text)) if w not in _STOPWORDS}


def candidate_sentences(target: str, text: str, k: int = 4) -> list[tuple[float, str]]:
    """Sentences of `text` that share the most content words (numbers included) with `target`."""
    want = _words(target)
    if not want:
        return []
    scored = []
    for sent in _SENT_SPLIT.split(text or ""):
        sent = sent.strip()
        if not 20 <= len(sent) <= 400:
            continue
        overlap = len(want & _words(sent)) / len(want)
        if overlap > 0:
            scored.append((round(overlap, 3), sent))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def snap_quote(claim: dict[str, Any], store: "ReceiptStore") -> bool:
    """Replace a paraphrased or missing quote with the closest real sentence from a cited page.

    The original is kept in quote_original and the claim can then reach at
    most "partial": the source says something close, not what was quoted.
    """
    quote = str(claim.get("quote", "")).strip()
    target = quote or str(claim.get("text", ""))
    for rid in claim.get("receipt_ids", []):
        rec = store.get(str(rid))
        if rec is None or rec.kind != "page":
            continue
        if quote:
            score, _ = match_quote(quote, rec.text)
            if score >= 0.9:
                return False  # already verbatim enough
            if score < 0.5:
                continue
        sentence, ratio = best_sentence(target, rec.text)
        if sentence and ratio >= 0.6:
            claim["quote_original"] = quote
            claim["quote"] = sentence
            claim["quote_snapped"] = True
            claim["snap_ratio"] = ratio
            return True
    return False


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
                       "highlight_found": r.highlight_found if r.kind == "page" else None,
                       "highlight_hidden": r.highlight_hidden if r.kind == "page" else ""})
    # The live page must confirm the quote: highlighted on screen, located in
    # the page but hidden from view (text-only evidence), or produced by code.
    visual = any(c.get("highlight_found") or c.get("highlight_hidden") or c.get("kind") == "code"
                 for c in checks if c.get("score", 0) >= 0.9)
    if best >= 0.9 and visual and not claim.get("quote_snapped"):
        status = "verified"
    elif best >= 0.6:
        status = "partial"
    else:
        status = "unverified"
    return {"status": status, "quote_score": best, "checks": checks}
