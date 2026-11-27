"""Client for Vultr Serverless Inference (OpenAI-compatible API).

All agent reasoning goes through this module. The API key lives only in
the control plane process; it is never passed to a sandbox.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

# Preference order used when LLM_MODEL / VISION_MODEL are not set. The
# live list comes from GET /models, so unknown ids are simply skipped.
_TEXT_PREFS = ["llama-3.3-70b", "qwen3", "qwen2.5-72b", "deepseek-v3", "gpt-oss", "kimi", "mistral", "llama"]
_VISION_PREFS = ["vl", "vision", "pixtral", "llava", "gemma-3"]


class LLMError(RuntimeError):
    pass


class VultrInference:
    def __init__(self) -> None:
        self.base = settings.inference_base_url
        self.key = settings.inference_api_key
        self._models: list[str] | None = None
        self.model = settings.llm_model
        self.vision_model = settings.vision_model

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    async def list_models(self) -> list[str]:
        if self._models is None:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(f"{self.base}/models", headers=self.headers)
                r.raise_for_status()
                self._models = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        return self._models

    async def resolve_models(self) -> tuple[str, str]:
        """Fill in model ids from the live model list when not configured."""
        if self.model and (self.vision_model or settings.vision_model == "none"):
            return self.model, self.vision_model
        try:
            models = await self.list_models()
        except httpx.HTTPError as exc:
            log.warning("could not list models: %s", exc)
            models = []
        lower = {m.lower(): m for m in models}
        if not self.model:
            for pref in _TEXT_PREFS:
                hit = next((orig for low, orig in lower.items() if pref in low and "vl" not in low), None)
                if hit:
                    self.model = hit
                    break
            if not self.model and models:
                self.model = models[0]
        if not self.vision_model:
            for pref in _VISION_PREFS:
                hit = next((orig for low, orig in lower.items() if pref in low), None)
                if hit:
                    self.vision_model = hit
                    break
        if not self.model:
            raise LLMError("no LLM_MODEL configured and the model list is empty")
        return self.model, self.vision_model

    async def chat(self, messages: list[dict[str, Any]], *, model: str | None = None,
                   max_tokens: int | None = None, temperature: float | None = None) -> str:
        if not model:
            model, _ = await self.resolve_models()
        body = {"model": model, "messages": messages,
                "max_tokens": max_tokens or settings.llm_max_tokens,
                "temperature": settings.llm_temperature if temperature is None else temperature}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.base}/chat/completions", headers=self.headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"inference error {r.status_code}: {r.text[:400]}")
        data = r.json()
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected inference response: {str(data)[:400]}") from exc
        return strip_reasoning(content)

    async def vision_check(self, claim: str, quote: str, image_b64: str, mime: str) -> dict[str, Any] | None:
        """Ask a vision model whether a screenshot supports a claim."""
        _, vmodel = await self.resolve_models()
        if not vmodel or mime == "image/svg+xml":
            return None
        prompt = ("You verify research receipts. Look at the screenshot of a web page. The highlighted text should "
                  "support the claim. Answer ONLY with JSON: {\"supports\": true|false, \"reason\": \"<short>\"}.\n"
                  f"Claim: {claim}\nExpected quote: {quote}")
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}}]}]
        try:
            raw = await self.chat(messages, model=vmodel, max_tokens=200, temperature=0)
            out = extract_json(raw)
            return {"model": vmodel, "supports": bool(out.get("supports")), "reason": str(out.get("reason", ""))[:300]}
        except (LLMError, ValueError, httpx.HTTPError) as exc:
            return {"model": vmodel, "supports": None, "reason": f"vision check failed: {exc}"[:300]}


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
