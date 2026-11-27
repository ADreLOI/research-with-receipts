"""Docker sandbox: the real containment boundary.

Per research run:
  * one long-lived "browser" container (Chromium + fetch/search), egress
    allowed, private/link-local IPs blocked by the worker;
  * one fresh "code" container per python call, with --network none.

Every container is non-root, read-only rootfs, all capabilities dropped,
no-new-privileges, memory/CPU/PID capped, and gets an empty environment.
Set SANDBOX_DOCKER_HOST=ssh://root@<vm> to run them on a separate,
throwaway Vultr VM instead of the app VM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from ..config import settings
from .base import Sandbox, SandboxInfo, ToolResult

log = logging.getLogger(__name__)

LABEL = "rwr.sandbox=1"


def _docker_env() -> dict[str, str]:
    # The docker CLI itself runs on the host; it needs PATH and HOME, and an
    # optional DOCKER_HOST. The containers get none of this.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/root")}
    if settings.sandbox_docker_host:
        env["DOCKER_HOST"] = settings.sandbox_docker_host
    elif os.environ.get("DOCKER_HOST"):
        env["DOCKER_HOST"] = os.environ["DOCKER_HOST"]
    return env


async def _docker(*args: str, stdin: bytes | None = None, timeout: float = 60.0) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_docker_env())
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out, err


async def docker_available() -> bool:
    try:
        code, _, _ = await _docker("info", "--format", "{{.ServerVersion}}", timeout=10)
        return code == 0
    except (FileNotFoundError, asyncio.TimeoutError):
        return False


async def image_present(image: str) -> bool:
    try:
        code, _, _ = await _docker("image", "inspect", image, timeout=15)
        return code == 0
    except (FileNotFoundError, asyncio.TimeoutError):
        return False


async def reap_orphans() -> int:
    """Remove sandbox containers left behind by a crashed process."""
    try:
        code, out, _ = await _docker("ps", "-aq", "--filter", f"label={LABEL}", timeout=15)
        ids = out.decode().split()
        if code == 0 and ids:
            await _docker("rm", "-f", *ids, timeout=60)
        return len(ids)
    except (FileNotFoundError, asyncio.TimeoutError):
        return 0


def _hardening(memory: str, cpus: str, pids: int, tmpfs_size: str) -> list[str]:
    flags = [
        "--memory", memory, "--memory-swap", memory,
        "--cpus", cpus, "--pids-limit", str(pids),
        "--read-only", "--tmpfs", f"/tmp:rw,nosuid,nodev,size={tmpfs_size},mode=1777",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10001:10001", "--label", LABEL,
    ]
    if settings.sandbox_runtime:
        flags += ["--runtime", settings.sandbox_runtime]
    return flags


class DockerSandbox(Sandbox):
    name = "docker"

    def __init__(self) -> None:
        self.run_id = ""
        self.container = ""
        self.limits = settings.limits
        self._code_calls = 0
        self._lock = asyncio.Lock()

    async def start(self, run_id: str) -> SandboxInfo:
        self.run_id = run_id
        if not await docker_available():
            raise RuntimeError("docker daemon unreachable" + (f" at {settings.sandbox_docker_host}"
                                                               if settings.sandbox_docker_host else ""))
        if not await image_present(settings.sandbox_image):
            raise RuntimeError(f"sandbox image {settings.sandbox_image!r} not found; run scripts/build_sandbox.sh")
        name = f"rwr-{run_id[:12]}-browser"
        args = ["run", "-d", "--name", name, "--label", f"rwr.run={run_id}",
                *_hardening(self.limits.browser_memory, self.limits.browser_cpus, self.limits.browser_pids, "512m"),
                "--shm-size", "256m", "--network", settings.sandbox_network,
                "-e", f"RWR_ALLOW_HOSTS={settings.sandbox_allow_hosts}",
                settings.sandbox_image]
        code, out, err = await _docker(*args, timeout=90)
        if code != 0:
            raise RuntimeError(f"failed to start sandbox: {err.decode()[:500]}")
        self.container = out.decode().strip()
        return SandboxInfo(
            backend="docker", isolated=True, container_id=self.container[:12], image=settings.sandbox_image,
            runtime=settings.sandbox_runtime or "runc",
            limits=self.limits.as_dict(),
            notes=("non-root, read-only rootfs, cap-drop ALL, no-new-privileges, empty env (no API keys)"
                   + (f", remote host {settings.sandbox_docker_host.split('@')[-1]}" if settings.sandbox_docker_host else "")))

    async def _exec_browser(self, tool: str, payload: dict) -> ToolResult:
        req = json.dumps({"tool": tool, **payload}).encode()
        t0 = time.monotonic()
        async with self._lock:  # one browser action at a time per run
            try:
                code, out, err = await _docker("exec", "-i", self.container, "python", "/opt/worker/worker.py",
                                               stdin=req, timeout=self.limits.tool_timeout_s)
            except asyncio.TimeoutError:
                # The worker may still be running; restart wipes it (and tmpfs).
                await _docker("restart", "-t", "0", self.container, timeout=60)
                return ToolResult(False, tool, error=f"sandbox timeout after {self.limits.tool_timeout_s}s; "
                                  "container restarted", duration_s=time.monotonic() - t0, contained="timeout")
        return self._parse(tool, code, out, err, time.monotonic() - t0)

    @staticmethod
    def _parse(tool: str, code: int, out: bytes, err: bytes, dt: float) -> ToolResult:
        try:
            msg = json.loads(out.decode() or "{}")
        except json.JSONDecodeError:
            msg = {}
        if not msg:
            contained = "killed (memory/pid limit)" if code in (137, -9) else f"worker exit {code}"
            return ToolResult(False, tool, error=f"{contained}: {err.decode()[-800:]}", duration_s=dt,
                              contained=contained if code in (137, -9) else "")
        if not msg.get("ok"):
            return ToolResult(False, tool, error=msg.get("error", "unknown error"), duration_s=dt)
        data = msg.get("data", {})
        contained = "timeout" if data.get("timed_out") else ""
        if tool == "python" and data.get("exit_code") in (137, -9) and not contained:
            contained = "killed (memory/pid limit)"
        return ToolResult(True, tool, data=data, duration_s=dt, contained=contained)

    async def search(self, query: str, max_results: int = 6) -> ToolResult:
        return await self._exec_browser("search", {"query": query, "max_results": max_results})

    async def fetch(self, url: str) -> ToolResult:
        return await self._exec_browser("fetch", {"url": url})

    async def screenshot(self, url: str, highlight: str = "") -> ToolResult:
        return await self._exec_browser("screenshot", {"url": url, "highlight": highlight})

    async def run_python(self, code: str) -> ToolResult:
        self._code_calls += 1
        name = f"rwr-{self.run_id[:12]}-code-{self._code_calls}-{uuid.uuid4().hex[:4]}"
        req = json.dumps({"tool": "python", "code": code, "timeout_s": self.limits.code_timeout_s}).encode()
        args = ["run", "--rm", "-i", "--name", name, "--label", f"rwr.run={self.run_id}",
                *_hardening(self.limits.code_memory, self.limits.code_cpus, self.limits.code_pids, "64m"),
                "--network", "none", settings.sandbox_image, "python", "/opt/worker/worker.py"]
        t0 = time.monotonic()
        try:
            code_, out, err = await _docker(*args, stdin=req, timeout=self.limits.code_timeout_s + 30)
        except asyncio.TimeoutError:
            await _docker("rm", "-f", name, timeout=30)
            return ToolResult(False, "python", error="hard timeout: container force-removed",
                              duration_s=time.monotonic() - t0, contained="timeout")
        return self._parse("python", code_, out, err, time.monotonic() - t0)

    async def destroy(self) -> None:
        if self.run_id:
            code, out, _ = await _docker("ps", "-aq", "--filter", f"label=rwr.run={self.run_id}", timeout=20)
            ids = out.decode().split()
            if ids:
                await _docker("rm", "-f", *ids, timeout=60)
            self.container = ""
