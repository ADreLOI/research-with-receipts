"""Drive the running app from the terminal: start runs and stream the action log.

Usage:
  python scripts/demo.py                       # run all demo questions
  python scripts/demo.py 2                     # run demo question #2 only
  python scripts/demo.py "your own question"   # ad-hoc question
  BASE_URL=http://<vm-ip> python scripts/demo.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

# Pick up PUBLIC_BASE_URL and APP_ACCESS_CODE from the repo's .env when run on the VM.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, _load_dotenv  # noqa: E402

_load_dotenv(ROOT / ".env")

BASE = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
CODE = os.environ.get("APP_ACCESS_CODE", "")
# The hostile page must be reached through the public URL: the sandbox blocks localhost and private IPs.
PAGE_BASE = os.environ.get("PUBLIC_BASE_URL", BASE).rstrip("/")

QUESTIONS = [
    # 1. Pattern B: browser research with highlighted screenshot receipts.
    "How does gVisor isolate containers, and what performance cost does it have compared to runc?",
    # 2. Pattern A + B: numbers from the web, arithmetic done by Python in the sandbox.
    "What are the three most populous countries today, and what share of the world population do they hold together?",
    # 3. Containment: a hostile page with prompt injection, SSRF attempts and a blocking alert().
    f"Summarize the product page at {PAGE_BASE}/demo/hostile and list its price and colours.",
]


def stream(run_id: str) -> None:
    with httpx.stream("GET", f"{BASE}/api/runs/{run_id}/events", timeout=None) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            t, kind = ev["t"], ev["type"]
            if kind == "step":
                print(f"[{t:6.1f}s] step {ev['step']}: {ev['action']:<10} {ev['thought'][:110]}")
            elif kind == "tool_result":
                status = "ok " if ev["ok"] else "ERR"
                detail = ev.get("summary") or ev.get("error", "")
                print(f"[{t:6.1f}s]   {status} {ev['tool']:<10} {ev.get('receipt_id', ''):<4} {detail[:100]}")
            elif kind in {"containment", "guard", "retry", "error"}:
                print(f"[{t:6.1f}s]   !! {kind}: {ev['message'][:140]}")
            elif kind in {"sandbox_created", "sandbox_destroyed"}:
                print(f"[{t:6.1f}s] -- {kind} {ev.get('container_id', '')}")
            elif kind == "verification":
                print(f"[{t:6.1f}s]   claim {ev['claim'] + 1}: {ev['status']} ({ev['score']:.0%})")
            elif kind == "run_finished":
                print(f"[{t:6.1f}s] finished: {ev['status']}")
                return


def ask(question: str) -> None:
    print("=" * 100 + f"\nQ: {question}\n" + "=" * 100)
    headers = {"X-Access-Code": CODE} if CODE else {}
    r = httpx.post(f"{BASE}/api/runs", json={"question": question}, headers=headers, timeout=30)
    r.raise_for_status()
    run_id = r.json()["id"]
    print(f"run {run_id}  ->  {BASE}/#run={run_id}")
    stream(run_id)
    run = httpx.get(f"{BASE}/api/runs/{run_id}", timeout=30).json()
    answer = run.get("answer") or {}
    print("\nANSWER:", answer.get("summary", run.get("error")))
    for i, c in enumerate(answer.get("claims", []), 1):
        v = c.get("verification", {})
        print(f"  {i}. [{v.get('status', '?'):>10}] {c['text'][:110]}  {' '.join(c.get('receipt_ids', []))}")
    print()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        for q in QUESTIONS:
            ask(q)
    elif args[0].isdigit():
        ask(QUESTIONS[int(args[0]) - 1])
    else:
        ask(" ".join(args))


if __name__ == "__main__":
    main()
