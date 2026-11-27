"""Export a run as a self-contained receipt bundle (zip).

Contents:
  manifest.json       question, answer, claims with verification, receipt index with SHA-256
  receipts/R<n>.json  full receipt (captured text, code, stdout, metadata)
  receipts/R<n>.<ext> screenshot or chart, byte-for-byte as captured
  report.html         readable report that works offline
  verify_bundle.py    stdlib-only script that recomputes every hash
"""

from __future__ import annotations

import html
import io
import json
import time
import zipfile
from typing import Any

from .receipts import HASH_FORMULA
from .runs import Run

VERIFY_SCRIPT = '''"""Verify a Research with Receipts bundle. Usage: python verify_bundle.py [bundle_dir]"""
import hashlib, json, pathlib, sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parent)
manifest = json.loads((root / "manifest.json").read_text())
bad = 0
for entry in manifest["receipts"]:
    r = json.loads((root / entry["file"]).read_text())
    image = (root / entry["image"]).read_bytes() if entry.get("image") else b""
    h = hashlib.sha256((r["url"] + "\\n" + r["text"] + "\\n" + r["code"] + "\\n" + r["stdout"]).encode() + image)
    ok = h.hexdigest() == entry["sha256"] == r["sha256"]
    bad += not ok
    print(("OK  " if ok else "BAD ") + entry["id"] + "  " + h.hexdigest())
print(f"{len(manifest['receipts']) - bad}/{len(manifest['receipts'])} receipts verified")
sys.exit(1 if bad else 0)
'''


def _report(run: Run, entries: list[dict[str, Any]]) -> str:
    a = run.answer or {}
    by_id = {e["id"]: e for e in entries}
    rows = []
    for i, c in enumerate(a.get("claims", []), 1):
        v = c.get("verification", {})
        ids = list(c.get("receipt_ids", [])) + ([c["evidence_id"]] if c.get("evidence_id") else [])
        links = " ".join(f'<a href="{html.escape(by_id[r]["image"] or by_id[r]["file"])}">{r}</a>'
                         for r in ids if r in by_id)
        img_id = c.get("evidence_id") or next((r for r in ids if r in by_id and by_id[r]["image"]), "")
        img = (f'<img src="{html.escape(by_id[img_id]["image"])}" alt="receipt {img_id}">'
               if img_id in by_id and by_id[img_id]["image"] else "")
        rows.append(f'<li><b class="{v.get("status", "")}">{v.get("status", "?")}</b> {html.escape(c["text"])} '
                    f'{links}<blockquote>{html.escape(c.get("quote", ""))}</blockquote>{img}</li>')
    srcs = "".join(f'<li><code>{e["id"]}</code> {html.escape(e.get("url") or e["tool"])} '
                   f'<small>{e["captured_at"]} · sha256 {e["sha256"]}</small></li>' for e in entries)
    mode = "<p><b>Replay</b> of a recorded run.</p>" if run.mode == "replay" else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Receipts: {html.escape(run.question[:60])}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;color:#161a22;background:#fff}}
img{{max-width:100%;border:1px solid #ddd;border-radius:6px;margin-top:6px}}li{{margin:0 0 18px}}
blockquote{{border-left:3px solid #f5b400;margin:6px 0;padding:2px 10px;color:#555}}
.verified{{color:#0f8a5f}}.partial{{color:#a86400}}.unverified{{color:#c0392b}}code,small{{font-family:monospace}}
small{{color:#666;word-break:break-all}}</style></head><body>
<h1>Research with Receipts</h1>{mode}<p><b>Question:</b> {html.escape(run.question)}</p>
<p>{html.escape(a.get("summary", run.error or ""))}</p><h2>Claims</h2><ol>{"".join(rows)}</ol>
<h2>Receipts</h2><ul>{srcs}</ul>
<p><small>Run {run.id} · model {html.escape(run.llm)} · exported {time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}.
Verify offline with <code>python verify_bundle.py</code>. Hash: {html.escape(HASH_FORMULA)}</small></p></body></html>"""


def build_bundle(run: Run) -> bytes:
    buf = io.BytesIO()
    entries: list[dict[str, Any]] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rec in run.receipts.items.values():
            data = rec.public(include_text=True)
            data.pop("has_image", None)
            name = f"receipts/{rec.id}.json"
            z.writestr(name, json.dumps(data, indent=1))
            image = ""
            if rec.image_file:
                image = f"receipts/{rec.image_file}"
                z.writestr(image, run.receipts.image_bytes(rec))
            entries.append({"id": rec.id, "kind": rec.kind, "tool": rec.tool, "url": rec.url, "title": rec.title,
                            "captured_at": rec.captured_at, "sandbox": rec.sandbox, "sha256": rec.sha256,
                            "file": name, "image": image})
        manifest = {"format": "research-with-receipts/1", "run_id": run.id, "question": run.question,
                    "mode": run.mode, "replay_of": run.replay_of, "llm": run.llm, "sandbox": run.sandbox,
                    "status": run.status, "answer": run.answer, "hash_formula": HASH_FORMULA,
                    "receipts": entries, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        z.writestr("report.html", _report(run, entries))
        z.writestr("verify_bundle.py", VERIFY_SCRIPT)
    return buf.getvalue()
