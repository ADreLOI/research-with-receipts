"""The agent loop: plan -> dispatch to sandbox -> observe -> repeat -> verify.

The control plane keeps the LLM key and the receipts. The sandbox does all
browsing and code execution. Every tool call is an event on the run log.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import httpx

from .cassettes import Cassette, Recorder
from .config import settings
from .llm import LLMError, extract_json
from .prompts import FORCE_FINAL, REPAIR, SYSTEM_PROMPT
from .providers import (LLMProvider, MockProvider, RecordingProvider, ReplayProvider, VultrProvider,
                        make_live_provider)
from .receipts import Receipt, match_quote, verify_claim
from .runs import Run
from .sandbox import Sandbox, ToolResult, make_sandbox
from .sandbox.replay_sandbox import RecordingSandbox, ReplaySandbox

log = logging.getLogger(__name__)

OBS_TEXT_LIMIT = 6000
INJECTION_RE = re.compile(
    r"(ignore (all |any )?(the )?(previous|prior|above) instructions|system (instruction|prompt|message)|"
    r"you are now|send (me |us )?(every|all|your) (api )?keys?|exfiltrat|rm -rf|os\.environ)", re.I)

_run_slots = asyncio.Semaphore(settings.max_concurrent_runs)


# ------------------------------------------------------------ policies

class Policy:
    """Turns the conversation into the next JSON action, whatever the provider."""

    def __init__(self, question: str, provider: LLMProvider) -> None:
        self.provider = provider
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(max_steps=settings.max_steps)},
            {"role": "user", "content": f"Question: {question}"},
        ]

    async def next_action(self, force_final: bool = False) -> dict[str, Any]:
        if force_final:
            self.messages.append({"role": "user", "content": FORCE_FINAL})
        last_err = ""
        for _ in range(3):
            raw = await self.provider.chat(self.messages)
            self.messages.append({"role": "assistant", "content": raw})
            try:
                action = extract_json(raw)
                if not isinstance(action.get("action"), str):
                    raise ValueError("missing 'action'")
                if not isinstance(action.get("args"), dict):
                    action["args"] = {}
                return action
            except (ValueError, json.JSONDecodeError) as exc:
                last_err = str(exc)
                self.messages.append({"role": "user", "content": REPAIR.format(error=last_err)})
        raise LLMError(f"model did not produce a valid action: {last_err}")

    def observe(self, step: int, tool: str, text: str) -> None:
        self.messages.append({"role": "user", "content": f"Observation for step {step} ({tool}):\n{text}"})


# ------------------------------------------------------------ tools

async def brave_search(query: str, n: int) -> ToolResult:
    """Search from the control plane so the API key never enters the sandbox."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                             headers={"X-Subscription-Token": settings.brave_api_key, "Accept": "application/json"})
    if r.status_code >= 400:
        return ToolResult(False, "search", error=f"brave search error {r.status_code}")
    results = [{"title": i.get("title", ""), "url": i.get("url", ""),
                "snippet": re.sub(r"<[^>]+>", "", i.get("description", ""))}
               for i in r.json().get("web", {}).get("results", [])[:n]]
    return ToolResult(True, "search", data={"provider": "brave (control plane)", "query": query, "results": results})


class Agent:
    def __init__(self, run: Run, cassette: Cassette | None = None) -> None:
        self.run = run
        self.cassette = cassette
        self.replay = cassette is not None
        self.recorder: Recorder | None = None
        if cassette is not None:
            self.provider: LLMProvider = ReplayProvider(cassette)
        else:
            self.provider = make_live_provider()
            if settings.record_runs:
                self.recorder = Recorder(run.question)
        self.policy = Policy(run.question, self.provider)
        self.sandbox: Sandbox | None = None
        self.sandbox_label = ""
        self.retries = 0

    async def _prepare_provider(self) -> None:
        """Check the live model is reachable; degrade gracefully if it is not."""
        if self.replay:
            return
        if isinstance(self.provider, VultrProvider):
            ok, detail = await self.provider.ping()
            if not ok:
                if settings.llm_fallback != "mock":
                    raise LLMError(f"Vultr Serverless Inference unreachable: {detail}")
                self.provider = MockProvider(note=f"fallback, Vultr unreachable: {detail[:80]}")
                self.run.emit("fallback", message=f"Vultr Serverless Inference unreachable ({detail[:160]}). "
                                                  "Continuing with the labelled mock policy (LLM_FALLBACK=mock).")
        if self.recorder:
            self.provider = RecordingProvider(self.provider, self.recorder)
        self.policy.provider = self.provider

    async def _make_sandbox(self) -> Sandbox:
        if self.cassette is not None:
            return ReplaySandbox(self.cassette)
        inner = await make_sandbox()
        return RecordingSandbox(inner, self.recorder) if self.recorder else inner

    # -- helpers

    def _sandbox_tag(self) -> str:
        return self.sandbox_label

    def _page_receipt(self, step: int, tool: str, res: ToolResult, highlight: str = "") -> Receipt:
        d = res.data
        rec = Receipt(id=self.run.receipts.next_id(), kind="page", tool=tool, step=step,
                      captured_at=d.get("captured_at", ""), sandbox=self._sandbox_tag(), url=d.get("url", ""),
                      title=d.get("title", ""), text=d.get("text", ""), highlight=highlight,
                      highlight_found=bool(d.get("highlight_found")),
                      blocked_requests=list(d.get("blocked_requests", [])))
        return self.run.receipts.add(rec, d.get("image_b64", ""), d.get("image_mime", ""))

    async def dispatch(self, step: int, action: str, args: dict[str, Any]) -> str:
        """Run one tool in the sandbox, record receipts and return the observation text."""
        assert self.sandbox is not None
        run = self.run
        if action == "search":
            query = str(args.get("query", ""))[:300]
            if settings.search_provider == "brave" and settings.brave_api_key and not self.replay:
                res = await brave_search(query, 6)
                if self.recorder:
                    self.recorder.tool_call("search", {"query": query}, res.ok, res.data, res.error,
                                            res.duration_s, res.contained)
            else:
                res = await self.sandbox.search(query, 6)
            self._emit_tool(step, action, res, summary=f"{len(res.data.get('results', []))} results via "
                                                       f"{res.data.get('provider', '?')}" if res.ok else "",
                            extra={"results": res.data.get("results", [])})
            if not res.ok:
                return f"ERROR: {res.error}"
            lines = [f"- title: {r['title']}\n  url: {r['url']}\n  snippet: {r['snippet'][:300]}"
                     for r in res.data.get("results", [])]
            return "Search results (leads only, not receipts):\n" + ("\n".join(lines) or "(no results)")

        if action in {"fetch", "screenshot"}:
            url = str(args.get("url", "")).strip()
            res = await (self.sandbox.fetch(url) if action == "fetch" else self.sandbox.screenshot(url))
            if not res.ok:
                self._emit_tool(step, action, res, extra={"url": url})
                return f"ERROR: {res.error}"
            rec = self._page_receipt(step, action, res)
            flagged = bool(INJECTION_RE.search(rec.text))
            self._emit_tool(step, action, res, receipt=rec, summary=f"{rec.title[:80]} ({len(rec.text)} chars)",
                            extra={"url": rec.url, "injection_suspected": flagged,
                                   "blocked_requests": rec.blocked_requests})
            if flagged:
                run.emit("guard", step=step, receipt_id=rec.id,
                         message="Page contains instruction-like text (possible prompt injection). It is passed to "
                                 "the model as untrusted data; the sandbox holds no secrets to steal.")
            text = rec.text[:OBS_TEXT_LIMIT]
            return (f"receipt_id: {rec.id}\nurl: {rec.url}\ntitle: {rec.title}\n"
                    f"<untrusted_content>\n{text}\n</untrusted_content>")

        if action == "python":
            code = str(args.get("code", ""))[:20000]
            res = await self.sandbox.run_python(code)
            if not res.ok:
                self._emit_tool(step, action, res, extra={"code": code})
                return f"ERROR: {res.error}"
            d = res.data
            rec = Receipt(id=run.receipts.next_id(), kind="code", tool="python", step=step,
                          captured_at=d.get("captured_at", ""), sandbox=self._sandbox_tag(), code=code,
                          stdout=d.get("stdout", ""), stderr=d.get("stderr", ""), exit_code=d.get("exit_code"))
            files = d.get("files") or []
            rec = run.receipts.add(rec, files[0]["b64"] if files else "", "image/png" if files else "")
            failed = d.get("exit_code") not in (0, None)
            self._emit_tool(step, action, res, receipt=rec,
                            summary=f"exit {d.get('exit_code')} in {d.get('duration_s')}s",
                            extra={"code": code, "stdout": rec.stdout[-1500:], "stderr": rec.stderr[-1500:]})
            if failed:
                self.retries += 1
                run.emit("retry", step=step, message="Code failed; stderr is fed back to the model for a fix.")
            return (f"receipt_id: {rec.id}\nexit_code: {rec.exit_code}\nstdout:\n{rec.stdout[-3000:]}\n"
                    f"stderr:\n{rec.stderr[-2000:]}")

        return f"ERROR: unknown action {action!r}. Use search, fetch, screenshot, python or final."

    def _emit_tool(self, step: int, tool: str, res: ToolResult, receipt: Receipt | None = None, summary: str = "",
                   extra: dict[str, Any] | None = None) -> None:
        self.run.emit("tool_result", step=step, tool=tool, ok=res.ok, error=res.error[:600],
                      duration_s=round(res.duration_s, 2), contained=res.contained,
                      receipt_id=receipt.id if receipt else "", summary=summary, **(extra or {}))
        if res.contained:
            self.run.emit("containment", step=step, tool=tool, message=f"Sandbox contained the action: "
                                                                       f"{res.contained}. Host unaffected.")

    # -- evidence pass

    async def capture_evidence(self, claims: list[dict[str, Any]]) -> None:
        """Re-open each cited page with the quote highlighted, so the receipt shows the exact sentence."""
        assert self.sandbox is not None
        shots = 0
        done: dict[tuple[str, str], str] = {}
        for idx, claim in enumerate(claims):
            quote = str(claim.get("quote", ""))
            for rid in claim.get("receipt_ids", []):
                rec = self.run.receipts.get(str(rid))
                if rec is None or rec.kind != "page" or not quote:
                    continue
                score, _ = match_quote(quote, rec.text)
                if score < 0.6:
                    continue
                key = (rec.url, quote)
                if key in done:
                    claim["evidence_id"] = done[key]
                    break
                if shots >= settings.max_evidence_shots:
                    break
                shots += 1
                self.run.emit("evidence", claim=idx, receipt_id=rec.id, message=f"Capturing highlighted "
                                                                                f"screenshot for claim {idx + 1}")
                res = await self.sandbox.screenshot(rec.url, highlight=quote)
                if res.ok:
                    ev = self._page_receipt(rec.step, "evidence", res, highlight=quote)
                    ev.evidence_for.append(idx)
                    claim["evidence_id"] = ev.id
                    done[key] = ev.id
                    self.run.emit("tool_result", step=rec.step, tool="evidence", ok=True, receipt_id=ev.id,
                                  duration_s=round(res.duration_s, 2), contained="",
                                  summary=f"highlight {'found' if ev.highlight_found else 'not found'} on page",
                                  url=ev.url)
                else:
                    self.run.emit("tool_result", step=rec.step, tool="evidence", ok=False, error=res.error[:300],
                                  duration_s=round(res.duration_s, 2), contained=res.contained, url=rec.url)
                break

    async def verify(self, claims: list[dict[str, Any]]) -> None:
        for idx, claim in enumerate(claims):
            ids = [str(r) for r in claim.get("receipt_ids", [])]
            if claim.get("evidence_id"):
                ids.append(claim["evidence_id"])
            result = verify_claim({**claim, "receipt_ids": ids}, self.run.receipts)
            ev = self.run.receipts.get(claim.get("evidence_id", "")) if claim.get("evidence_id") else None
            if ev and ev.image_file and ev.image_mime != "image/svg+xml":
                img = base64.b64encode((self.run.receipts.dir / ev.image_file).read_bytes()).decode()
                vc = await self.provider.vision_check(claim.get("text", ""), claim.get("quote", ""), img,
                                                      ev.image_mime)
                if vc:
                    result["vision"] = vc
                    if vc.get("supports") is False and result["status"] == "verified":
                        result["status"] = "partial"
            claim["verification"] = result
            self.run.emit("verification", claim=idx, status=result["status"], score=result["quote_score"],
                          vision=result.get("vision"))

    # -- main loop

    async def run_loop(self) -> None:
        run = self.run
        await self._prepare_provider()
        info_llm = await self.provider.describe()
        run.llm = info_llm.label
        run.mode = "replay" if self.replay else "live"
        if self.cassette is not None:
            run.replay_of = self.cassette.summary()
        run.emit("run_started", question=run.question, llm=run.llm, mode=run.mode, replay_of=run.replay_of)
        self.sandbox = await self._make_sandbox()
        info = await self.sandbox.start(run.id)
        run.sandbox = info.as_dict()
        self.sandbox_label = f"{info.backend}:{info.container_id}"
        run.emit("sandbox_created", **info.as_dict())
        try:
            final: dict[str, Any] | None = None
            for step in range(1, settings.max_steps + 2):
                force = step > settings.max_steps
                action = await self.policy.next_action(force_final=force)
                name = str(action.get("action", "")).lower()
                args = action.get("args") or {}
                run.emit("step", step=step, thought=str(action.get("thought", ""))[:600], action=name,
                         args={k: (v[:1500] if isinstance(v, str) else v) for k, v in args.items()}
                         if name != "final" else {})
                if name == "final":
                    final = args
                    break
                obs = await self.dispatch(step, name, args)
                self.policy.observe(step, name, obs)
            if final is None:
                raise LLMError("agent ended without a final answer")
            claims = [c for c in final.get("claims", []) if isinstance(c, dict) and c.get("text")]
            run.emit("drafted", n_claims=len(claims))
            await self.capture_evidence(claims)
            await self.verify(claims)
            counts = {s: sum(1 for c in claims if c["verification"]["status"] == s)
                      for s in ("verified", "partial", "unverified")}
            run.answer = {"summary": str(final.get("summary", "")), "claims": claims, "counts": counts,
                          "retries": self.retries}
            run.emit("final", counts=counts)
        finally:
            await self.sandbox.destroy()
            run.emit("sandbox_destroyed", container_id=info.container_id,
                     message="Replay finished; nothing was executed." if self.replay
                     else "Sandbox and all its state destroyed.")
            if self.recorder and run.answer is not None:
                self.recorder.save(run.dir / "cassette", run.llm, run.sandbox)


async def execute_run(run: Run, cassette: Cassette | None = None) -> None:
    async with _run_slots:
        run.status = "running"
        agent = Agent(run, cassette)
        try:
            await asyncio.wait_for(agent.run_loop(), timeout=settings.run_timeout_s)
            run.status = "done"
        except asyncio.TimeoutError:
            run.status, run.error = "failed", f"run exceeded {settings.run_timeout_s}s"
            run.emit("error", message=run.error)
            if agent.sandbox:
                await agent.sandbox.destroy()
        except Exception as exc:  # noqa: BLE001 - surface every failure to the UI
            log.exception("run %s failed", run.id)
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:800]
            run.emit("error", message=run.error)
        finally:
            run.emit("run_finished", status=run.status)
            run.save()
