"""Record the demo questions live and promote them to replayable cassettes.

Run this ON THE VM (repo root), against the running app, shortly before the demo:
  python scripts/record_demo.py                  # all demo questions
  python scripts/record_demo.py 1 3              # only questions 1 and 3
Then check the answers in the UI and commit demo/cassettes/.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.cassettes import SHIPPED_DIR  # noqa: E402  (also loads .env, so PUBLIC_BASE_URL is set)
from demo import QUESTIONS  # noqa: E402
from promote_run import promote  # noqa: E402

BASE = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
CODE = os.environ.get("APP_ACCESS_CODE", "")


def record(question: str) -> None:
    headers = {"X-Access-Code": CODE} if CODE else {}
    r = httpx.post(f"{BASE}/api/runs", json={"question": question, "mode": "live"}, headers=headers, timeout=30)
    r.raise_for_status()
    run_id = r.json()["id"]
    print(f"recording {run_id}: {question[:80]}")
    while True:
        run = httpx.get(f"{BASE}/api/runs/{run_id}", timeout=30).json()
        if run["status"] in {"done", "failed"}:
            break
        time.sleep(2)
    if run["status"] != "done":
        print(f"  FAILED: {run.get('error')}  (nothing promoted)")
        return
    counts = (run.get("answer") or {}).get("counts", {})
    llm = str(run.get("llm") or "")
    print(f"  done: {counts}  llm={llm}")
    if "mock" in llm.lower() and "--allow-mock" not in sys.argv:
        print("  NOT promoted: this run used the mock policy, not Vultr Inference. Fix the key/model "
              "(scripts/doctor.py) and record again, or pass --allow-mock.")
        return
    if not counts.get("verified"):
        print("  warning: no verified claims; check this run in the UI before committing it")
    print(f"  promoted -> {promote(run_id, SHIPPED_DIR)}")


def main() -> None:
    picks = [int(a) for a in sys.argv[1:] if a.isdigit()] or list(range(1, len(QUESTIONS) + 1))
    for i in picks:
        record(QUESTIONS[i - 1])
    print("\nReview the runs in the UI, then: git add demo/cassettes && git commit -m 'Record demo cassettes'")


if __name__ == "__main__":
    main()
