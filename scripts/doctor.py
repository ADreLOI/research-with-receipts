"""One-shot health report for a deployment. Safe to paste into a chat: it never prints secrets.

Usage (on the VM):
  sudo -u rwr /opt/rwr/.venv/bin/python scripts/doctor.py            # everything
  sudo -u rwr /opt/rwr/.venv/bin/python scripts/doctor.py --quick    # skip the sandbox tool calls

It checks, in order: configuration, Docker and the sandbox image,
Vultr Inference (models, a JSON round trip and one real agent step with
the real system prompt), the sandbox tools from inside a container
(search, fetch, screenshot, python with no network), the running app
and the demo recordings. Every line is PASS, WARN or FAIL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# app.config loads ROOT/.env into os.environ on import.
from app.cassettes import library  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import LLMError, extract_json  # noqa: E402
from app.normalize import normalize_action  # noqa: E402
from app.prompts import SYSTEM_PROMPT  # noqa: E402
from app.providers import VultrProvider  # noqa: E402
from app.sandbox.docker_sandbox import DockerSandbox, _docker, docker_available, image_present  # noqa: E402

SECRETS = ("VULTR_INFERENCE_API_KEY", "APP_ACCESS_CODE", "BRAVE_SEARCH_API_KEY", "VULTR_API_KEY")
PLAIN = ("PUBLIC_BASE_URL", "LLM_MODEL", "LLM_FALLBACK_MODEL", "VISION_MODEL", "SANDBOX_BACKEND", "SANDBOX_RUNTIME",
         "SANDBOX_DOCKER_HOST", "SEARCH_PROVIDER", "DEMO_MODE")

# Only trust an explicit setting here; the built-in default is localhost.
PUBLIC = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
results: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"[{status}] {name:<22} {detail}", flush=True)


def check_config() -> None:
    for key in SECRETS:
        val = os.environ.get(key, "").strip()
        if key == "VULTR_INFERENCE_API_KEY":
            report("PASS" if val else "FAIL", "env." + key, "set (not shown)" if val else "missing: the app runs the mock")
        else:
            report("PASS" if val else "WARN", "env." + key, "set (not shown)" if val else "not set")
    for key in PLAIN:
        val = os.environ.get(key, "").strip()
        report("PASS", "env." + key, val or "(default)")
    if PUBLIC and not PUBLIC.startswith("https://"):
        report("WARN", "config.https", "PUBLIC_BASE_URL is not https; crypto.subtle in the browser needs https "
                                       "(the Verify button falls back to the server)")


async def check_docker() -> bool:
    if not await docker_available():
        report("FAIL", "docker.daemon", "unreachable (is the rwr user in the docker group? is dockerd running?)")
        return False
    code, out, _ = await _docker("version", "--format", "{{.Server.Version}}", timeout=20)
    report("PASS", "docker.daemon", f"server {out.decode().strip()}"
           + (f" via {settings.sandbox_docker_host}" if settings.sandbox_docker_host else ""))
    if not await image_present(settings.sandbox_image):
        report("FAIL", "docker.image", f"{settings.sandbox_image} missing: run scripts/build_sandbox.sh")
        return False
    report("PASS", "docker.image", settings.sandbox_image)
    if settings.sandbox_runtime:
        code, out, _ = await _docker("info", "--format", "{{json .Runtimes}}", timeout=20)
        runtimes = json.loads(out.decode() or "{}") if code == 0 else {}
        ok = settings.sandbox_runtime in runtimes
        report("PASS" if ok else "FAIL", "docker.runtime",
               settings.sandbox_runtime + ("" if ok else f" not installed (have: {', '.join(runtimes)})"))
    return True


async def check_inference() -> None:
    if not settings.inference_api_key:
        report("FAIL", "llm", "no VULTR_INFERENCE_API_KEY, skipped")
        return
    p = VultrProvider()
    ok, detail = await p.ping()
    report("PASS" if ok else "FAIL", "llm.reachable", f"{settings.inference_base_url} ({detail})")
    if not ok:
        return
    try:
        models = await p.list_models()
        report("PASS" if models else "WARN", "llm.models", f"{len(models)}: {', '.join(models)[:400]}")
        model, vision = await p.resolve_models()
        report("PASS", "llm.picked", f"text={model} vision={vision or '(none)'}")
    except LLMError as exc:
        report("FAIL", "llm.models", str(exc))
        return
    t0 = time.monotonic()
    try:
        raw = await p.chat([{"role": "user", "content": 'Reply with exactly this JSON and nothing else: {"ok": true}'}],
                           max_tokens=60, temperature=0)
        report("PASS", "llm.json", f"{time.monotonic() - t0:.1f}s via {p.active_model}: {extract_json(raw)}")
    except (LLMError, ValueError) as exc:
        report("FAIL", "llm.json", str(exc)[:300])
        return
    # One real agent turn: does the model follow the action protocol?
    t0 = time.monotonic()
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(max_steps=settings.max_steps)},
                {"role": "user", "content": "Question: What is the population of Amsterdam?"}]
    try:
        raw = await p.chat(messages)
        action = normalize_action(extract_json(raw))
        args = json.dumps(action.get("args", {}))[:120]
        good = action["action"] in {"search", "fetch", "screenshot"}
        report("PASS" if good else "WARN", "llm.agent_step",
               f"{time.monotonic() - t0:.1f}s action={action['action']} args={args}")
    except (LLMError, ValueError) as exc:
        report("FAIL", "llm.agent_step", f"{exc}; raw reply starts: {raw[:200]!r}" if "raw" in locals() else str(exc))


async def check_sandbox() -> None:
    sb = DockerSandbox()
    try:
        info = await sb.start("doctor")
        report("PASS", "sandbox.start", f"{info.container_id[:12]} runtime={info.runtime or 'runc'}")
        res = await sb.search("Amsterdam population", 5)
        provider = res.data.get("provider", "")
        n = len(res.data.get("results", []))
        status = "PASS" if res.ok and n and provider.startswith("duckduckgo") else "WARN" if res.ok and n else "FAIL"
        report(status, "sandbox.search", f"{provider or res.error} -> {n} results ({res.duration_s:.1f}s)"
               + ("; DuckDuckGo gave nothing from this IP, Wikipedia fallback in use" if status == "WARN" else ""))
        res = await sb.fetch("https://en.wikipedia.org/wiki/Amsterdam")
        report("PASS" if res.ok else "FAIL", "sandbox.fetch",
               f"{len(res.data.get('text', ''))} chars ({res.duration_s:.1f}s)" if res.ok else res.error[:200])
        res = await sb.screenshot("https://en.wikipedia.org/wiki/Amsterdam", "capital and most populated city")
        report("PASS" if res.ok else "FAIL", "sandbox.screenshot",
               f"{len(res.data.get('image_b64', ''))} b64 bytes, highlight_found={res.data.get('highlight_found')} "
               f"({res.duration_s:.1f}s)" if res.ok else res.error[:200])
        res = await sb.run_python("import socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), 3)\n"
                                  "    print('NETWORK OPEN')\nexcept OSError:\n    print('no network')\n"
                                  "print(sum(range(10)))")
        out = res.data.get("stdout", "")
        ok = res.ok and "no network" in out and "45" in out
        report("PASS" if ok else "FAIL", "sandbox.python", out.strip().replace("\n", " | ")[:120] or res.error[:200])
        if PUBLIC:
            res = await sb.fetch(PUBLIC + "/demo/hostile")
            report("PASS" if res.ok else "WARN", "sandbox.hostile_page",
                   "reachable from the sandbox" if res.ok else f"not reachable: {res.error[:150]}")
    except Exception as exc:  # noqa: BLE001
        report("FAIL", "sandbox", f"{type(exc).__name__}: {exc}"[:300])
    finally:
        await sb.destroy()


def check_app(base: str) -> None:
    try:
        h = httpx.get(f"{base}/api/health", timeout=10).json()
    except (httpx.HTTPError, ValueError) as exc:
        report("FAIL", "app.health", f"{base}: {exc} (systemctl status rwr)")
        return
    report("PASS" if h.get("sandbox_backend") == "docker" else "WARN", "app.sandbox",
           f"{h.get('sandbox_backend')} runtime={h.get('sandbox_runtime')}")
    st = h.get("llm_status", {})
    report("PASS" if st.get("live") and st.get("ok") else "WARN", "app.llm",
           f"{st.get('provider')} model={h.get('llm_model')} ({st.get('detail', '')})")
    report("PASS", "app.demo_mode", f"{h.get('demo_mode')}, access code required={h.get('access_code_required')}")
    if PUBLIC:
        try:
            r = httpx.get(PUBLIC + "/api/health", timeout=10)
            report("PASS" if r.status_code == 200 else "FAIL", "app.public_url", f"{PUBLIC} -> HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            report("WARN", "app.public_url", f"{PUBLIC}: {exc} (try from a laptop too)")
    else:
        report("WARN", "app.public_url", "PUBLIC_BASE_URL not set; the hostile demo question needs it")


def check_recordings() -> None:
    recs = library.all()
    if not recs:
        report("WARN", "recordings", "none: run scripts/record_demo.py so the demo has a safety net")
    for c in recs:
        s = c.summary()
        seed = "seed" in (s.get("note") or "").lower() or s.get("sandbox_backend") != "docker"
        report("WARN" if seed else "PASS", "recording", f"{s['slug']}: llm={s['llm']} sandbox={s['sandbox_backend']} "
               f"steps={s['steps']} tools={s['tool_calls']}" + (" (seed, re-record on the VM)" if seed else ""))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="skip the sandbox tool calls")
    ap.add_argument("--app", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    args = ap.parse_args()
    print(f"rwr doctor  {time.strftime('%Y-%m-%d %H:%M:%S %Z')}  repo={ROOT}")
    check_config()
    docker_ok = await check_docker()
    await check_inference()
    if docker_ok and not args.quick:
        await check_sandbox()
    check_app(args.app.rstrip("/"))
    check_recordings()
    counts = {s: sum(1 for r in results if r[0] == s) for s in ("PASS", "WARN", "FAIL")}
    print(f"\nsummary: {counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
