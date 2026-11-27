"""Deterministic offline provider.

It plays the role of the model by reading the conversation so far and
emitting the next JSON action. Same parsing path as a real model, so the
whole agent loop is exercised without any key or network.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import LLMProvider, ProviderInfo

_OBS = re.compile(r"^Observation for step (\d+) \((\w+)\):\n", re.S)


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, note: str = "no VULTR_INFERENCE_API_KEY") -> None:
        self.note = note

    async def describe(self) -> ProviderInfo:
        return ProviderInfo(provider="mock policy", live=False, note=self.note)

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int | None = None,
                   temperature: float | None = None) -> str:
        return json.dumps(self._decide(messages))

    # ---------------------------------------------------------------- logic

    @staticmethod
    def _state(messages: list[dict[str, Any]]) -> dict[str, Any]:
        question = ""
        leads: list[str] = []
        pages: list[dict[str, str]] = []
        code_receipt, code_stdout, force = "", "", False
        for m in messages:
            content = m.get("content")
            if m.get("role") != "user" or not isinstance(content, str):
                continue
            if content.startswith("Question: ") and not question:
                question = content[len("Question: "):]
                continue
            if content.startswith("You have used all your tool calls"):
                force = True
                continue
            hit = _OBS.match(content)
            if not hit:
                continue
            tool, text = hit.group(2), content[hit.end():]
            if tool == "search":
                leads = re.findall(r"url: (\S+)", text)
            elif tool in {"fetch", "screenshot"} and "receipt_id:" in text:
                rid = re.search(r"receipt_id: (R\d+)", text).group(1)
                body = text.split("<untrusted_content>", 1)[-1].split("</untrusted_content>", 1)[0]
                pages.append({"receipt": rid, "text": body})
            elif tool == "python" and "receipt_id:" in text:
                code_receipt = re.search(r"receipt_id: (R\d+)", text).group(1)
                m2 = re.search(r"stdout:\n(.*?)\nstderr:", text, re.S)
                code_stdout = m2.group(1) if m2 else ""
        url = re.search(r"https?://\S+", question)
        return {"question": question, "leads": leads, "pages": pages, "code_receipt": code_receipt,
                "code_stdout": code_stdout, "force": force,
                "direct_url": url.group(0).rstrip(").,") if url else "",
                "n_obs": sum(1 for m in messages if isinstance(m.get("content"), str)
                             and m["content"].startswith("Observation for step"))}

    def _decide(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        s = self._state(messages)
        if not s["force"]:
            if s["n_obs"] == 0:
                if s["direct_url"]:
                    return {"thought": "The question names a URL, so I open it directly.", "action": "screenshot",
                            "args": {"url": s["direct_url"]}}
                return {"thought": "Start with a web search.", "action": "search", "args": {"query": s["question"]}}
            if s["leads"] and len(s["pages"]) < min(2, len(s["leads"])):
                url = s["leads"][len(s["pages"])]
                return {"thought": f"Open source #{len(s['pages']) + 1} and capture a screenshot.",
                        "action": "screenshot", "args": {"url": url}}
            if s["pages"] and not s["code_receipt"]:
                words = {p["receipt"]: len(p["text"].split()) for p in s["pages"]}
                code = (f"words = {json.dumps(words)}\n"
                        "total = sum(words.values())\n"
                        "print(f'Captured {len(words)} sources totalling {total} words')\n")
                return {"thought": "Use the sandbox to compute a quick statistic over the sources.",
                        "action": "python", "args": {"code": code}}
        return {"thought": "I have enough receipts to answer.", "action": "final", "args": self._final(s)}

    @staticmethod
    def _final(s: dict[str, Any]) -> dict[str, Any]:
        qwords = set(re.findall(r"[a-z]{4,}", s["question"].lower()))
        claims, cites = [], []
        for p in s["pages"]:
            sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", p["text"]) if len(x.strip()) > 40]
            if not sentences:
                continue
            ranked = sorted(sentences, key=lambda x: -len(qwords & set(re.findall(r"[a-z]{4,}", x.lower()))))
            for x in ranked[:2]:
                claims.append({"text": x, "receipt_ids": [p["receipt"]], "quote": x})
            cites.append(f"[{p['receipt']}]")
        if s["code_receipt"] and s["code_stdout"].strip():
            out = s["code_stdout"].strip()
            claims.append({"text": out, "receipt_ids": [s["code_receipt"]], "quote": out})
        summary = (f"Mock answer assembled from {len(s['pages'])} captured sources {' '.join(cites)}. "
                   "Set VULTR_INFERENCE_API_KEY to let a model on Vultr Serverless Inference write the answer.")
        return {"summary": summary, "claims": claims}
