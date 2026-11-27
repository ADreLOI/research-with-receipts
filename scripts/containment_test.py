"""Run the containment battery against a running app and print the verdicts.

Usage: BASE_URL=http://localhost:8000 python scripts/containment_test.py
"""

import json
import os

import httpx

BASE = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
CODE = os.environ.get("APP_ACCESS_CODE", "")

with httpx.stream("GET", f"{BASE}/api/containment-test", params={"code": CODE} if CODE else None,
                  timeout=None) as r:
    for line in r.iter_lines():
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        if ev["type"] == "sandbox":
            print(f"sandbox: {ev['backend']} {ev['container_id']} isolated={ev['isolated']}")
        elif ev["type"] == "attack":
            mark = "CONTAINED" if ev["contained"] else "NOT CONTAINED"
            print(f"[{mark:>13}] {ev['title']:<45} {ev['note']} ({ev['duration_s']}s)")
        elif ev["type"] in {"done", "error"}:
            print(ev.get("message"), "host_ok=" + str(ev.get("host_ok", "?")))
