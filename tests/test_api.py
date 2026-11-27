import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .helpers import override


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _wait(client, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"done", "failed"}:
            return run
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_health_reports_modes(client):
    h = client.get("/api/health").json()
    assert h["sandbox_backend"] == "mock" and h["sandbox_isolated"] is False
    assert h["llm_status"]["provider"] == "mock"
    assert isinstance(h["recordings"], list)


def test_full_run_over_http_and_receipt_endpoints(client):
    r = client.post("/api/runs", json={"question": "How does gVisor isolate containers?", "mode": "live"})
    assert r.status_code == 200 and r.json()["mode"] == "live"
    run = _wait(client, r.json()["id"])
    assert run["status"] == "done" and run["answer"]["claims"]
    rid = run["receipts"][0]["id"]
    full = client.get(f"/api/runs/{run['id']}/receipts/{rid}").json()
    assert full["sha256"]
    v = client.get(f"/api/runs/{run['id']}/receipts/{rid}/verify").json()
    assert v["match"] is True and v["recomputed"] == full["sha256"]
    img = client.get(f"/api/runs/{run['id']}/receipts/{rid}/image")
    assert img.status_code == 200 and "default-src 'none'" in img.headers["content-security-policy"]
    events = client.get(f"/api/runs/{run['id']}/events").text
    assert '"type": "run_finished"' in events


def test_replay_mode_requires_a_recording(client):
    r = client.post("/api/runs", json={"question": "Something nobody recorded?", "mode": "replay"})
    assert r.status_code == 404


def test_replay_of_shipped_recording(client):
    q = "Summarize the product page at https://judges.example/demo/hostile and list its price and colours."
    with override(replay_speed=0.0):
        r = client.post("/api/runs", json={"question": q, "mode": "replay"})
        run = _wait(client, r.json()["id"])
    assert r.json()["mode"] == "replay"
    assert run["status"] == "done" and run["mode"] == "replay"
    assert run["sandbox"]["backend"] == "replay"
    assert any(e["type"] == "guard" for e in run["events"])


def test_auto_mode_replays_when_no_live_model(client):
    q = "Summarize the product page at http://x.example/demo/hostile and list its price and colours."
    with override(replay_speed=0.0, demo_mode="auto"):
        r = client.post("/api/runs", json={"question": q})
    assert r.json()["mode"] == "replay"
    _wait(client, r.json()["id"])


def test_input_validation_and_404s(client):
    assert client.post("/api/runs", json={"question": "hi"}).status_code == 422
    assert client.post("/api/runs", json={"question": "valid question", "mode": "turbo"}).status_code == 422
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/receipts/R1").status_code == 404


def test_access_code_is_enforced(client):
    with override(access_code="letmein"):
        assert client.post("/api/runs", json={"question": "Is this gated?"}).status_code == 401
        assert client.get("/api/health").json()["access_code_required"] is True  # the UI shows the field from this
        assert client.post("/api/access", headers={"X-Access-Code": "wrong"}).status_code == 401
        assert client.post("/api/access", headers={"X-Access-Code": "letmein"}).json() == {"ok": True}
        ok = client.post("/api/runs", json={"question": "Is this gated?"}, headers={"X-Access-Code": "letmein"})
        assert ok.status_code == 200
        _wait(client, ok.json()["id"])


def test_hostile_page_is_served(client):
    html = client.get("/demo/hostile").text
    assert "ignore all previous instructions" in html and "169.254.169.254" in html


def test_export_bundle_verifies_offline(client, tmp_path):
    import io
    import subprocess
    import sys
    import zipfile

    r = client.post("/api/runs", json={"question": "How does gVisor isolate containers?", "mode": "live"})
    run = _wait(client, r.json()["id"])
    resp = client.get(f"/api/runs/{run['id']}/export")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    zipfile.ZipFile(io.BytesIO(resp.content)).extractall(tmp_path)
    names = {p.name for p in tmp_path.rglob("*")}
    assert {"manifest.json", "report.html", "verify_bundle.py", "R1.json"} <= names
    ok = subprocess.run([sys.executable, "-I", str(tmp_path / "verify_bundle.py"), str(tmp_path)],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert f"{len(run['receipts'])}/{len(run['receipts'])} receipts verified" in ok.stdout
    # Tamper with one receipt: the verifier must catch it.
    import json as _json

    p = tmp_path / "receipts" / "R1.json"
    data = _json.loads(p.read_text())
    data["text"] = data["text"] + " (quietly edited)"
    p.write_text(_json.dumps(data))
    bad = subprocess.run([sys.executable, "-I", str(tmp_path / "verify_bundle.py"), str(tmp_path)],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "BAD R1" in bad.stdout
