"""Runtime configuration, read exclusively from environment variables.

No secret has a default value. If a key is missing the app degrades to a
clearly labelled mock mode instead of failing, so the project can be
developed and demoed offline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so we do not need python-dotenv.

    Existing environment variables always win over the file.
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


ROOT = Path(__file__).resolve().parent.parent
_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class SandboxLimits:
    """Resource caps applied to every sandbox container."""

    browser_memory: str = field(default_factory=lambda: _env("SANDBOX_BROWSER_MEMORY", "1g"))
    browser_cpus: str = field(default_factory=lambda: _env("SANDBOX_BROWSER_CPUS", "1.0"))
    browser_pids: int = field(default_factory=lambda: _env_int("SANDBOX_BROWSER_PIDS", 512))
    code_memory: str = field(default_factory=lambda: _env("SANDBOX_CODE_MEMORY", "256m"))
    code_cpus: str = field(default_factory=lambda: _env("SANDBOX_CODE_CPUS", "0.5"))
    code_pids: int = field(default_factory=lambda: _env_int("SANDBOX_CODE_PIDS", 64))
    tool_timeout_s: float = field(default_factory=lambda: _env_float("SANDBOX_TOOL_TIMEOUT_S", 45.0))
    code_timeout_s: float = field(default_factory=lambda: _env_float("SANDBOX_CODE_TIMEOUT_S", 10.0))

    def as_dict(self) -> dict:
        return {
            "browser": {"memory": self.browser_memory, "cpus": self.browser_cpus, "pids": self.browser_pids,
                        "network": "egress only, private ranges blocked", "timeout_s": self.tool_timeout_s},
            "code": {"memory": self.code_memory, "cpus": self.code_cpus, "pids": self.code_pids,
                     "network": "none", "timeout_s": self.code_timeout_s},
        }


@dataclass(frozen=True)
class Settings:
    # --- Vultr Serverless Inference (OpenAI-compatible) ---
    inference_base_url: str = field(
        default_factory=lambda: _env("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1").rstrip("/"))
    inference_api_key: str = field(default_factory=lambda: _env("VULTR_INFERENCE_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    vision_model: str = field(default_factory=lambda: _env("VISION_MODEL"))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.2))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 1800))
    # auto: vultr when VULTR_INFERENCE_API_KEY is set, else mock. vultr | mock force one.
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto").lower())
    # Second model tried when the primary is missing or keeps failing.
    llm_fallback_model: str = field(default_factory=lambda: _env("LLM_FALLBACK_MODEL"))
    llm_max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 2))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT_S", 90.0))
    # What a live run does when Vultr is unreachable at start and no recording
    # matches: "mock" runs the labelled mock policy, "none" fails the run.
    llm_fallback: str = field(default_factory=lambda: _env("LLM_FALLBACK", "mock").lower())

    # --- Sandbox ---
    # "docker" (real isolation) or "mock" (offline dev only, NOT isolated).
    # "auto" picks docker when the docker CLI can reach a daemon.
    sandbox_backend: str = field(default_factory=lambda: _env("SANDBOX_BACKEND", "auto").lower())
    sandbox_image: str = field(default_factory=lambda: _env("SANDBOX_IMAGE", "rwr-sandbox:latest"))
    # Optional gVisor runtime ("runsc") for a second isolation layer.
    sandbox_runtime: str = field(default_factory=lambda: _env("SANDBOX_RUNTIME"))
    # Optional remote Docker host, e.g. ssh://root@<sandbox-vm-ip>, so that
    # sandboxes run on a separate throwaway Vultr VM rather than the app VM.
    sandbox_docker_host: str = field(default_factory=lambda: _env("SANDBOX_DOCKER_HOST"))
    sandbox_network: str = field(default_factory=lambda: _env("SANDBOX_BROWSER_NETWORK", "bridge"))
    # Hosts the browser may reach even if they resolve to private IPs
    # (useful for a local hostile-page demo). Comma separated.
    sandbox_allow_hosts: str = field(default_factory=lambda: _env("SANDBOX_ALLOW_HOSTS"))
    limits: SandboxLimits = field(default_factory=SandboxLimits)

    # --- Search ---
    # "auto": DuckDuckGo HTML from inside the sandbox, Wikipedia as fallback.
    # "brave": Brave Search API called from the control plane (the key never
    # enters the sandbox).
    search_provider: str = field(default_factory=lambda: _env("SEARCH_PROVIDER", "auto").lower())
    brave_api_key: str = field(default_factory=lambda: _env("BRAVE_SEARCH_API_KEY"))

    # --- Agent ---
    max_steps: int = field(default_factory=lambda: _env_int("AGENT_MAX_STEPS", 12))
    run_timeout_s: float = field(default_factory=lambda: _env_float("AGENT_RUN_TIMEOUT_S", 420.0))
    max_concurrent_runs: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_RUNS", 3))
    max_evidence_shots: int = field(default_factory=lambda: _env_int("MAX_EVIDENCE_SCREENSHOTS", 8))

    # --- Demo safety net (record / replay) ---
    # live: always run for real. replay: replay a recording whenever one
    # matches the question. auto: run live, but replay when the live model
    # is unavailable and a recording matches. Replays are always labelled.
    demo_mode: str = field(default_factory=lambda: _env("DEMO_MODE", "auto").lower())
    # Multiplier on recorded latencies during replay (0 = instant).
    replay_speed: float = field(default_factory=lambda: _env_float("REPLAY_SPEED", 0.6))
    # Record every live run into its run directory so it can be promoted.
    record_runs: bool = field(default_factory=lambda: _env_bool("RECORD_RUNS", True))

    # --- Web app ---
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "data"))))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))
    # Optional shared code required to start runs on a public deployment.
    access_code: str = field(default_factory=lambda: _env("APP_ACCESS_CODE"))

    @property
    def llm_enabled(self) -> bool:
        """True when live runs should use Vultr Serverless Inference."""
        if _env_bool("FORCE_MOCK_LLM", False) or self.llm_provider == "mock":
            return False
        return bool(self.inference_api_key)


settings = Settings()
