# Submission: Research with Receipts

> Fill in the remaining links before submitting. Everything else is ready to paste.

- **Repository:** https://github.com/ADreLOI/research-with-receipts (branch `claude/research-with-receipts`)
- **Live demo:** `<public URL on the Vultr VM>`
- **Video (2 min):** `<video URL>`
- **Team:** `<names>`

## Tagline

An AI research agent that shows its receipts: every claim links to evidence captured by a real browser inside a disposable Vultr sandbox.

## What it does

You ask a question. The agent plans its research with a model on **Vultr Serverless Inference**, then dispatches every action to an isolated sandbox: web search, page fetches, a headless Chromium that takes screenshots, and Python for any arithmetic. The answer comes back as a short summary plus a list of atomic claims. Each claim cites **receipts**: the source URL, the exact quote, a screenshot of the page with that quote highlighted, a timestamp, and a SHA-256 hash. Each claim is then verified mechanically against its receipts and labelled verified, partial or unverified. Anyone can click **Verify SHA-256** to recompute the hash in their own browser.

It covers both execution patterns: Pattern B (sandboxed browser use with screenshot trails and vision verification) and Pattern A (sandboxed code execution with a visible self-heal loop when code fails).

## Why it matters

Research agents are persuasive, and that's the problem: you can't tell a real citation from a confident guess. And an agent that browses the open web constantly ingests hostile content. Receipts make the output auditable; containment makes the execution safe to ship.

## How Vultr is used

- **Vultr Cloud Compute VM**: the FastAPI control plane, agent loop, receipts store, and the Docker host for sandboxes, behind Caddy.
- **Vultr Serverless Inference**: all reasoning (OpenAI-compatible API), plus an optional vision model that checks each highlighted screenshot. The provider auto-picks models from `/v1/models`, retries with backoff and can switch to a fallback model.
- **Vultr as the sandbox fabric**: sandboxes are containers on Vultr compute, never in the app process. `scripts/vultr_sandbox_vm.py` uses the Vultr API to create a dedicated sandbox VM (Docker, egress guard, SSH locked to the app VM, sandbox image built by cloud-init), points the app at it over SSH, and destroys it afterwards.

## Containment

- One browser container per run, one fresh container per Python call, all destroyed at the end; orphans are reaped on startup.
- Non-root user, read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, memory, CPU and PID caps, per-tool and per-run timeouts. Optional gVisor runtime.
- No secrets inside: containers start with an empty environment. The inference key and any search key stay in the control plane.
- Python containers have no network at all. The browser container can reach the web but every request, including sub-resources and redirects, is checked: private, loopback and link-local addresses (cloud metadata at 169.254.169.254) are blocked, with an iptables guard on the VM as a second layer.
- Web content is fenced as untrusted data; instruction-like text is flagged in the UI.
- **Containment moment:** a built-in test throws 8 attacks at a fresh sandbox (steal keys, `rm -rf /`, infinite loop, fork bomb, 4 GB allocation, network exfiltration, metadata SSRF, localhost access). All 8 are contained, and the host is untouched. A hostile demo page adds prompt injection, SSRF attempts and a blocking `alert()`.

## Production touches

- One-click receipt bundle: a zip with every receipt, screenshot, an offline HTML report and a stdlib-only `verify_bundle.py` that recomputes every hash.
- Live action log over Server-Sent Events, phase stepper, receipts gallery with claim-to-receipt hover linking, run history with shareable links.
- Record and replay: every live run is recorded, and any run can be replayed offline through the same pipeline, clearly labelled as a replay. Our demo can't be killed by venue Wi-Fi.
- Optional access code for the public URL, health endpoint, systemd service, one-shot VM setup script.
- 68 automated tests that run offline (agent loop, receipts integrity, providers, replay, API), plus `scripts/doctor.py`, a one-command health report for the deployed VM.

## Architecture

```
Browser ──HTTPS──> Caddy ──> FastAPI control plane (Vultr VM)
                               │  agent loop · receipts · SSE · record/replay
                               ├──> Vultr Serverless Inference (text + vision)
                               └──> docker run/exec (local or ssh:// remote Vultr VM)
                                      ├─ browser container per run: Chromium + Playwright, private IPs blocked
                                      └─ python container per call: --network none, 256 MB, 10 s
```

## Tech stack

Python 3.12, FastAPI, httpx, Docker, Playwright + Chromium, pandas/matplotlib in the sandbox, vanilla JS front end (no build step), Caddy, systemd, pytest.

## Challenges

- Highlighting the exact quote on arbitrary pages: line breaks and inline tags defeat naive text search, so the sandbox matches against whitespace-free DOM text and maps back to a DOM range.
- Keeping search keyless inside the sandbox: DuckDuckGo HTML from the sandbox with a Wikipedia fallback; keyed search, if enabled, runs in the control plane.
- Making the demo deterministic without faking it: record/replay at the model and sandbox boundaries, with receipts and verification recomputed and replays labelled.

## What's next

- One Vultr instance per task through the Vultr API, created and destroyed by the control plane.
- Approve-before-submit gates for any irreversible browser action.
- Signing receipt bundles with a server key, so a bundle proves where it came from as well as that it is intact.
