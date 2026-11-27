"""FastAPI control plane: REST + Server-Sent Events + static web UI."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import execute_run
from .cassettes import library
from .config import ROOT, settings
from .containment import run_battery
from .providers import live_status
from .runs import registry
from .sandbox import resolve_backend
from .sandbox.docker_sandbox import reap_orphans

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rwr")

WEB = ROOT / "web"
DEMO = ROOT / "demo"
_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    backend = await resolve_backend()
    if backend == "docker":
        n = await reap_orphans()
        if n:
            log.info("removed %d orphaned sandbox containers", n)
    registry.load_saved()
    status = await live_status()
    log.info("sandbox backend=%s llm=%s (%s)", backend, status["provider"], status["detail"])
    yield


app = FastAPI(title="Research with Receipts", version="1.0", lifespan=lifespan)


class RunRequest(BaseModel):
    question: str = Field(min_length=5, max_length=600)
    # auto: follow DEMO_MODE. live: never replay. replay: require a recording.
    mode: str = Field(default="auto", pattern="^(auto|live|replay)$")


async def _live_available() -> bool:
    status = await live_status()
    return status["live"] and status["ok"]


def _check_code(code: str | None) -> None:
    if settings.access_code and code != settings.access_code:
        raise HTTPException(status_code=401, detail="access code required")


@app.get("/api/health")
async def health() -> dict:
    backend = await resolve_backend()
    return {"ok": True, "sandbox_backend": backend, "sandbox_isolated": backend == "docker",
            "sandbox_runtime": settings.sandbox_runtime or ("runc" if backend == "docker" else "local"),
            "remote_sandbox_host": bool(settings.sandbox_docker_host),
            "llm": "vultr-serverless-inference" if settings.llm_enabled else "mock",
            "llm_status": await live_status(),
            "llm_fallback": settings.llm_fallback,
            "llm_model": settings.llm_model or ("auto" if settings.llm_enabled else ""),
            "vision_model": settings.vision_model or ("auto" if settings.llm_enabled else ""),
            "inference_base_url": settings.inference_base_url,
            "limits": settings.limits.as_dict(), "max_steps": settings.max_steps,
            "access_code_required": bool(settings.access_code),
            "demo_mode": settings.demo_mode,
            "recordings": [c.summary() for c in library.all()],
            "public_base_url": settings.public_base_url}


@app.post("/api/runs")
async def create_run(body: RunRequest, x_access_code: str | None = Header(default=None)) -> dict:
    _check_code(x_access_code)
    question = body.question.strip()
    cassette = library.find(question)
    use_replay = False
    if body.mode == "replay":
        if cassette is None:
            raise HTTPException(404, "no recording matches this question")
        use_replay = True
    elif body.mode == "auto" and cassette is not None:
        use_replay = settings.demo_mode == "replay" or (settings.demo_mode == "auto" and not await _live_available())
    run = registry.create(question)
    task = asyncio.create_task(execute_run(run, cassette if use_replay else None))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"id": run.id, "mode": "replay" if use_replay else "live"}


@app.get("/api/recordings")
async def list_recordings() -> list[dict]:
    return [c.summary() for c in library.all()]


@app.get("/api/runs")
async def list_runs() -> list[dict]:
    return registry.list()


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = registry.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run.public()


@app.get("/api/runs/{run_id}/receipts/{rid}")
async def get_receipt(run_id: str, rid: str) -> dict:
    run = registry.get(run_id)
    rec = run.receipts.get(rid) if run else None
    if not rec:
        raise HTTPException(404, "receipt not found")
    return rec.public(include_text=True)


@app.get("/api/runs/{run_id}/receipts/{rid}/verify")
async def verify_receipt(run_id: str, rid: str) -> dict:
    """Recompute the receipt's SHA-256 server-side (the UI also does it in the browser)."""
    run = registry.get(run_id)
    rec = run.receipts.get(rid) if run else None
    if not rec:
        raise HTTPException(404, "receipt not found")
    return run.receipts.verify_hash(rec)


@app.get("/api/runs/{run_id}/receipts/{rid}/image")
async def receipt_image(run_id: str, rid: str) -> FileResponse:
    run = registry.get(run_id)
    rec = run.receipts.get(rid) if run else None
    if not rec or not rec.image_file:
        raise HTTPException(404, "no image")
    path = (run.receipts.dir / rec.image_file).resolve()
    if run.receipts.dir.resolve() not in path.parents:
        raise HTTPException(400, "bad path")
    # Mock screenshots are SVG; serve them with a strict CSP so they can't run script.
    return FileResponse(path, media_type=rec.image_mime or "application/octet-stream",
                        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                                 "Cache-Control": "public, max-age=86400"})


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    run = registry.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")

    async def gen():
        q = run.subscribe()
        try:
            for ev in list(run.events):
                yield _sse(ev)
            if run.status in {"done", "failed"} and run.events and run.events[-1]["type"] == "run_finished":
                return
            seen = len(run.events)
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if ev["seq"] < seen:
                    continue
                yield _sse(ev)
                if ev["type"] == "run_finished":
                    return
        finally:
            run.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/containment-test")
async def containment_test(code: str | None = None) -> StreamingResponse:
    _check_code(code)

    async def gen():
        try:
            async for item in run_battery():
                yield _sse(item)
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/demo/hostile", response_class=HTMLResponse)
async def hostile_page() -> HTMLResponse:
    """A deliberately hostile page used in the containment demo."""
    return HTMLResponse((DEMO / "hostile.html").read_text())


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((WEB / "index.html").read_text())


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

