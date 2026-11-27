import base64
import importlib.util
import json
from pathlib import Path

import httpx

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vultr_sandbox_vm.py"
spec = importlib.util.spec_from_file_location("vultr_sandbox_vm", SCRIPT)
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


def test_cloud_init_embeds_image_sources_and_locks_ssh():
    ci = vm.cloud_init("ssh-ed25519 AAAA test", "203.0.113.7")
    assert ci.startswith("#cloud-config")
    assert "ssh-ed25519 AAAA test" in ci
    assert "ufw allow from 203.0.113.7 to any port 22" in ci
    assert "169.254.0.0/16" in ci and "docker build -t rwr-sandbox:latest" in ci
    worker_b64 = ci.split("path: /opt/rwr-sandbox/worker.py")[1].split("content: ")[1].split("\n")[0]
    assert "def host_is_safe" in base64.b64decode(worker_b64).decode()
    assert "VULTR_API_KEY" not in ci and "VULTR_INFERENCE_API_KEY" not in ci


def test_client_creates_lists_and_deletes_with_tag():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.content))
        if req.url.path == "/v2/os":
            return httpx.Response(200, json={"os": [{"id": 1, "name": "Ubuntu 22.04 LTS x64"},
                                                    {"id": 2284, "name": "Ubuntu 24.04 LTS x64"}]})
        if req.method == "POST":
            return httpx.Response(202, json={"instance": {"id": "abc", "status": "pending"}})
        if req.method == "GET" and req.url.path == "/v2/instances":
            assert req.url.params["tag"] == vm.TAG
            return httpx.Response(200, json={"instances": [{"id": "abc"}]})
        return httpx.Response(204)

    v = vm.Vultr("k", transport=httpx.MockTransport(handler))
    assert v.os_id("Ubuntu 24.04") == 2284
    inst = v.create(region="ams", plan="vc2-2c-4gb", os_id=2284, label="x", user_data="#cloud-config\n")
    assert inst["id"] == "abc"
    body = json.loads(calls[1][2])
    assert body["tags"] == [vm.TAG] and base64.b64decode(body["user_data"]).startswith(b"#cloud-config")
    assert [i["id"] for i in v.list()] == ["abc"]
    v.delete("abc")
    assert calls[-1][:2] == ("DELETE", "/v2/instances/abc")
