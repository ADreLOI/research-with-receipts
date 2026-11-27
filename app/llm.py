"""Helpers shared by all LLM providers: error type and robust JSON extraction."""

from __future__ import annotations

import json
import re
from typing import Any


class LLMError(RuntimeError):
    pass


class ContextTooLong(LLMError):
    """The conversation no longer fits the model's context window."""


_CTX_HINTS = ("context length", "context_length", "context window", "maximum context", "too many tokens",
              "prompt is too long", "input is too long", "reduce the length", "exceeds the maximum")


def is_context_error(status: int, body: str) -> bool:
    return status in (400, 413, 422) and any(h in body.lower() for h in _CTX_HINTS)


_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_reasoning(text: str) -> str:
    """Remove <think> blocks that some reasoning models emit."""
    text = _THINK.sub("", text)
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (tolerates code fences and chatter)."""
    text = strip_reasoning(text)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object in reply")
