"""Make real model output usable.

Models drift from the requested JSON shape in predictable ways: "tool"
instead of "action", arguments at the top level or as a JSON string,
receipt ids written as "R1, R2" or 1, code wrapped in markdown fences.
These helpers map all of that onto the one shape the agent loop expects.
"""

from __future__ import annotations

import json
import re
from typing import Any

ACTION_ALIASES = {
    "search": "search", "web_search": "search", "search_web": "search", "websearch": "search", "google": "search",
    "fetch": "fetch", "fetch_page": "fetch", "get": "fetch", "read": "fetch", "read_page": "fetch",
    "open_url": "fetch", "http_get": "fetch",
    "screenshot": "screenshot", "browse": "screenshot", "browser": "screenshot", "open": "screenshot",
    "open_page": "screenshot", "visit": "screenshot", "navigate": "screenshot", "screenshot_page": "screenshot",
    "python": "python", "run_python": "python", "code": "python", "execute_python": "python", "python_exec": "python",
    "run_code": "python", "exec": "python",
    "final": "final", "final_answer": "final", "answer": "final", "finish": "final", "done": "final",
    "respond": "final",
}
_PRIMARY_ARG = {"search": "query", "fetch": "url", "screenshot": "url", "python": "code"}
_ARG_KEYS = ("query", "url", "code", "summary", "claims", "q", "link", "href", "source")
_FENCE = re.compile(r"^\s*```(?:python|py)?\s*\n(.*?)\n?```\s*$", re.S)


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def normalize_action(obj: dict[str, Any]) -> dict[str, Any]:
    """Return {"thought", "action", "args"} or raise ValueError if no action can be found."""
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else {}
    raw_name = obj.get("action") or obj.get("tool") or obj.get("name") or fn.get("name") or ""
    if not raw_name and ("claims" in obj or "summary" in obj or "answer" in obj):
        raw_name = "final"
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("missing 'action'")
    key = raw_name.strip().lower().replace("-", "_").replace(" ", "_")
    action = ACTION_ALIASES.get(key, key)

    raw_args = next((obj[k] for k in ("args", "arguments", "parameters", "input", "action_input") if k in obj),
                    fn.get("arguments"))
    args = _as_dict(raw_args)
    if args is None:
        args = {}
        if isinstance(raw_args, str) and raw_args.strip() and action in _PRIMARY_ARG:
            args[_PRIMARY_ARG[action]] = raw_args.strip()
    args = dict(args)
    for k in _ARG_KEYS:  # arguments placed at the top level
        if k in obj and k not in args:
            args[k] = obj[k]
    if action == "final" and "answer" in obj and "summary" not in args and isinstance(obj["answer"], str):
        args["summary"] = obj["answer"]
    # Synonyms for the primary argument.
    if action in ("fetch", "screenshot") and "url" not in args:
        for alt in ("link", "href", "source", "page"):
            if isinstance(args.get(alt), str):
                args["url"] = args[alt]
                break
    if action == "search" and "query" not in args and isinstance(args.get("q"), str):
        args["query"] = args["q"]
    if action == "python" and isinstance(args.get("code"), str):
        m = _FENCE.match(args["code"])
        if m:
            args["code"] = m.group(1)
    if action in ("fetch", "screenshot") and isinstance(args.get("url"), str):
        args["url"] = args["url"].strip().strip("<>\"'")
    thought = obj.get("thought") or obj.get("reasoning") or obj.get("plan") or ""
    return {"thought": str(thought), "action": action, "args": args}


_RID = re.compile(r"R?\s*(\d+)", re.I)


def _receipt_ids(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, bool) or item is None:
            continue
        for num in _RID.findall(str(item)):
            rid = f"R{int(num)}"
            if rid not in out:
                out.append(rid)
    return out


def normalize_claims(final_args: dict[str, Any]) -> list[dict[str, Any]]:
    """Coerce the model's claims into [{"text", "receipt_ids", "quote"}]."""
    raw = final_args.get("claims") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    claims: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            ids = _receipt_ids(re.findall(r"\[(R?\d+)\]", item))
            text = re.sub(r"\s*\[R?\d+\]", "", item).strip()
            if text:
                claims.append({"text": text, "receipt_ids": ids, "quote": ""})
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("claim") or item.get("statement") or ""
        ids_raw = next((item[k] for k in ("receipt_ids", "receipts", "receipt_id", "sources", "citations", "cite",
                                          "evidence_ids") if k in item), [])
        quote = item.get("quote") or item.get("evidence") or item.get("excerpt") or item.get("snippet") or ""
        if isinstance(text, str) and text.strip():
            claims.append({"text": text.strip(), "receipt_ids": _receipt_ids(ids_raw),
                           "quote": quote.strip() if isinstance(quote, str) else ""})
    return claims
