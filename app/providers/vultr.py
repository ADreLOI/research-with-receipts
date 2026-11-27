"""Vultr Serverless Inference provider (OpenAI-compatible API).

Environment:
  VULTR_INFERENCE_API_KEY   required for live runs (never passed to a sandbox)
  VULTR_INFERENCE_BASE_URL  default https://api.vultrinference.com/v1
  LLM_MODEL                 model id; empty = auto-pick from GET /models
  LLM_FALLBACK_MODEL        tried when the primary model is missing or failing
  VISION_MODEL              vision model id; empty = auto-pick, "none" = off
  LLM_MAX_RETRIES           retries on 429 / 5xx / timeouts (exponential backoff)
  LLM_TIMEOUT_S             per-request timeout
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..config import settings
from ..llm import ContextTooLong, LLMError, extract_json, is_context_error, strip_reasoning
from .base import LLMProvider, ProviderInfo

log = logging.getLogger(__name__)

# Preference order used when LLM_MODEL / VISION_MODEL are not set. The
# live list comes from GET /models, so unknown ids are simply skipped.
_TEXT_PREFS = ["llama-3.3-70b", "qwen3", "qwen2.5-72b", "deepseek-v3", "gpt-oss", "kimi", "mistral", "llama"]
_VISION_PREFS = ["vl", "vision", "pixtral", "llava", "gemma-3"]
# Ids that are clearly not chat models (embeddings, safety classifiers,
# speech, image generation). Never auto-pick them for the agent.
_NON_CHAT = ("embed", "rerank", "guard", "whisper", "tts", "speech", "audio", "flux", "stable-diffusion", "sdxl",
             "image-gen", "moderation", "bge", "e5-")
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_PING_TTL_S = 60.0


class ModelUnavailable(LLMError):
    """The requested model does not exist or is not enabled for this key."""


class VultrProvider(LLMProvider):
    name = "vultr"
    # Shared across instances: one reachability check per minute is plenty.
    _ping_cache: tuple[float, bool, str] | None = None

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = settings.inference_base_url
        self.key = settings.inference_api_key
        self._transport = transport  # injectable for tests
        self._models: list[str] | None = None
        self.model = settings.llm_model
        self.fallback_model = settings.llm_fallback_model
        self._vision_disabled = settings.vision_model.lower() == "none"
        self.vision_model = "" if self._vision_disabled else settings.vision_model
        self.active_model = ""

    # ---------------------------------------------------------------- http

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def _request(self, method: str, path: str, *, json: dict | None = None, timeout: float | None = None,
                       retries: int | None = None) -> httpx.Response:
        """HTTP call with exponential backoff on transient failures."""
        retries = settings.llm_max_retries if retries is None else retries
        delay = 1.0
        last = ""
        for attempt in range(retries + 1):
            try:
                async with self._client(timeout or settings.llm_timeout_s) as client:
                    r = await client.request(method, f"{self.base}{path}", headers=self.headers, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 401:
                    raise LLMError("Vultr Inference rejected the API key (401). Check VULTR_INFERENCE_API_KEY.")
                if r.status_code not in _RETRY_STATUS:
                    return r
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                retry_after = r.headers.get("retry-after", "")
                if retry_after.isdigit():
                    delay = max(delay, min(float(retry_after), 20.0))
            if attempt < retries:
                log.warning("vultr inference %s %s failed (%s); retry %d in %.1fs", method, path, last,
                            attempt + 1, delay)
                await asyncio.sleep(delay)
                delay *= 2
        raise LLMError(f"Vultr Inference unavailable after {retries + 1} attempts: {last}")

    # ---------------------------------------------------------------- models

    async def list_models(self) -> list[str]:
        if self._models is None:
            r = await self._request("GET", "/models", timeout=20)
            if r.status_code >= 400:
                raise LLMError(f"could not list models: HTTP {r.status_code}")
            self._models = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
        return self._models

    async def resolve_models(self) -> tuple[str, str]:
        """Fill in model ids from the live model list when not configured."""
        if self.model and (self.vision_model or self._vision_disabled):
            return self.model, self.vision_model
        try:
            models = await self.list_models()
        except LLMError as exc:
            log.warning("%s", exc)
            models = []
        lower = {m.lower(): m for m in models if not any(bad in m.lower() for bad in _NON_CHAT)}
        if not self.model:
            for pref in _TEXT_PREFS:
                hit = next((orig for low, orig in lower.items() if pref in low and "vl" not in low), None)
                if hit:
                    self.model = hit
                    break
            if not self.model and lower:
                self.model = next(iter(lower.values()))
        if not self.vision_model and not self._vision_disabled:
            for pref in _VISION_PREFS:
                hit = next((orig for low, orig in lower.items() if pref in low), None)
                if hit:
                    self.vision_model = hit
                    break
        if not self.model:
            raise LLMError("No LLM_MODEL configured and GET /models returned nothing. Set LLM_MODEL "
                           "(see scripts/check_inference.py).")
        return self.model, self.vision_model

    async def describe(self) -> ProviderInfo:
        model, vision = await self.resolve_models()
        return ProviderInfo(provider="vultr", model=self.active_model or model, vision_model=vision, live=True,
                            note=f"fallback model: {self.fallback_model}" if self.fallback_model else "")

    async def ping(self) -> tuple[bool, str]:
        cached = VultrProvider._ping_cache
        if cached and time.monotonic() - cached[0] < _PING_TTL_S:
            return cached[1], cached[2]
        try:
            r = await self._request("GET", "/models", timeout=8, retries=0)
            ok = r.status_code < 400
            detail = "reachable" if ok else f"HTTP {r.status_code}"
        except LLMError as exc:
            ok, detail = False, str(exc)
        VultrProvider._ping_cache = (time.monotonic(), ok, detail)
        return ok, detail

    # ---------------------------------------------------------------- chat

    async def _complete(self, model: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float,
                        retries: int | None = None) -> str:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        r = await self._request("POST", "/chat/completions", json=body, retries=retries)
        if is_context_error(r.status_code, r.text):
            raise ContextTooLong(f"model {model!r}: conversation too long: {r.text[:200]}")
        if r.status_code in (400, 404) and "model" in r.text.lower():
            raise ModelUnavailable(f"model {model!r} unavailable: {r.text[:200]}")
        if r.status_code >= 400:
            raise LLMError(f"inference error {r.status_code}: {r.text[:400]}")
        data = r.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected inference response: {str(data)[:400]}") from exc
        content = strip_reasoning(message.get("content") or "")
        if not content.strip():
            # Reasoning models sometimes put everything in a separate field,
            # or spend the whole token budget thinking.
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            if isinstance(reasoning, str) and "{" in reasoning:
                return reasoning[reasoning.rfind("{\"thought\""):] if '{"thought"' in reasoning else reasoning
            if choice.get("finish_reason") == "length":
                raise LLMError(f"model {model!r} hit max_tokens before answering; raise LLM_MAX_TOKENS or pick a "
                               "non-reasoning model with LLM_MODEL")
            raise LLMError(f"model {model!r} returned an empty reply")
        return content

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int | None = None,
                   temperature: float | None = None) -> str:
        model, _ = await self.resolve_models()
        max_tokens = max_tokens or settings.llm_max_tokens
        temperature = settings.llm_temperature if temperature is None else temperature
        primary = self.active_model or model
        try:
            text = await self._complete(primary, messages, max_tokens, temperature)
            self.active_model = primary
            return text
        except ContextTooLong:
            raise  # the caller shortens the conversation; another model would not help
        except LLMError as exc:
            if not self.fallback_model or self.fallback_model == primary:
                raise
            log.warning("primary model %s failed (%s); switching to fallback %s", primary, exc, self.fallback_model)
            text = await self._complete(self.fallback_model, messages, max_tokens, temperature)
            self.active_model = self.fallback_model  # stick with the model that works for this run
            return text

    async def vision_check(self, claim: str, quote: str, image_b64: str, mime: str) -> dict[str, Any] | None:
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
            raw = await self._complete(vmodel, messages, 200, 0, retries=1)
            out = extract_json(raw)
            return {"model": vmodel, "supports": bool(out.get("supports")), "reason": str(out.get("reason", ""))[:300]}
        except (LLMError, ValueError) as exc:
            # Vision is a bonus check; never fail the run because of it.
            return {"model": vmodel, "supports": None, "reason": f"vision check skipped: {exc}"[:300]}
