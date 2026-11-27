"""Use the Vultr API to run sandboxes on a separate, throwaway Vultr VM.

The control plane then drives Docker on that VM over SSH
(SANDBOX_DOCKER_HOST=ssh://root@<ip>), so hostile pages and model-written
code never even share a kernel with the app. Destroy the VM after the
event (or per session) and nothing it touched survives.

Run it on the app VM as the user that runs the app, so the SSH key and
known_hosts entry land where the docker CLI will look for them:

  sudo -u rwr /opt/rwr/.venv/bin/python scripts/vultr_sandbox_vm.py create --write-env
  sudo systemctl restart rwr
  sudo -u rwr /opt/rwr/.venv/bin/python scripts/vultr_sandbox_vm.py status
  sudo -u rwr /opt/rwr/.venv/bin/python scripts/vultr_sandbox_vm.py destroy --all

Needs VULTR_API_KEY (Vultr portal > Account > API; allow the app VM's IP in
the API access control list). The key stays on the app VM and is never
copied to the sandbox VM.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import _load_dotenv  # noqa: E402

API = "https://api.vultr.com/v2"
TAG = "rwr-sandbox"
READY_MARKER = "/var/lib/cloud/rwr-ready"


def api_key() -> str:
    _load_dotenv(ROOT / ".env")
    key = os.environ.get("VULTR_API_KEY", "").strip()
    if not key:
        sys.exit("VULTR_API_KEY is not set (Vultr portal > Account > API). It is only needed for this script.")
    return key


class Vultr:
    def __init__(self, key: str, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=30,
                                 transport=transport)

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise SystemExit(f"Vultr API {r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}")
        return r

    def os_id(self, name: str) -> int:
        r = self._check(self.http.get("/os", params={"per_page": 500}))
        for item in r.json().get("os", []):
            if name.lower() in item.get("name", "").lower() and "x64" in item.get("name", "").lower():
                return int(item["id"])
        raise SystemExit(f"no OS image matching {name!r} x64")

    def create(self, *, region: str, plan: str, os_id: int, label: str, user_data: str) -> dict:
        body = {"region": region, "plan": plan, "os_id": os_id, "label": label, "hostname": label, "tags": [TAG],
                "backups": "disabled", "user_data": base64.b64encode(user_data.encode()).decode()}
        return self._check(self.http.post("/instances", json=body)).json()["instance"]

    def get(self, instance_id: str) -> dict:
        return self._check(self.http.get(f"/instances/{instance_id}")).json()["instance"]

    def list(self) -> list[dict]:
        return self._check(self.http.get("/instances", params={"tag": TAG, "per_page": 100})).json().get(
            "instances", [])

    def delete(self, instance_id: str) -> None:
        self._check(self.http.delete(f"/instances/{instance_id}"))


def ensure_ssh_key() -> str:
    """Public key of the current user; created if missing (the docker CLI uses it for ssh://)."""
    ssh_dir = Path.home() / ".ssh"
    key = ssh_dir / "id_ed25519"
    if not key.exists():
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "rwr-control-plane", "-f", str(key)],
                       check=True, capture_output=True)
    return (ssh_dir / "id_ed25519.pub").read_text().strip()


def my_public_ip() -> str:
    try:
        return httpx.get("https://api.ipify.org", timeout=10).text.strip()
    except httpx.HTTPError:
        return ""


def cloud_init(pubkey: str, allow_ssh_from: str) -> str:
    """cloud-config that installs Docker, the egress guard and builds the sandbox image."""
    dockerfile = (ROOT / "sandbox_image" / "Dockerfile").read_text()
    worker = (ROOT / "sandbox_image" / "worker.py").read_text()

    def b64(s: str) -> str:
        return base64.b64encode(s.encode()).decode()

    ssh_rule = f"ufw allow from {allow_ssh_from} to any port 22 proto tcp" if allow_ssh_from else "ufw allow OpenSSH"
    return f"""#cloud-config
ssh_authorized_keys:
  - {pubkey}
write_files:
  - path: /opt/rwr-sandbox/Dockerfile
    encoding: b64
    content: {b64(dockerfile)}
  - path: /opt/rwr-sandbox/worker.py
    encoding: b64
    content: {b64(worker)}
runcmd:
  - curl -fsSL https://get.docker.com | sh
  - systemctl enable --now docker
  - for n in 169.254.0.0/16 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do iptables -I DOCKER-USER -d $n -m conntrack --ctstate NEW -j DROP; done
  - iptables -I DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  - ufw default deny incoming
  - {ssh_rule}
  - ufw --force enable
  - docker build -t rwr-sandbox:latest /opt/rwr-sandbox
  - touch {READY_MARKER}
"""


def wait_ready(v: Vultr, instance_id: str, timeout_s: int = 1200) -> str:
    deadline = time.time() + timeout_s
    ip = ""
    while time.time() < deadline:
        inst = v.get(instance_id)
        ip = inst.get("main_ip", "")
        print(f"  status={inst.get('status')} power={inst.get('power_status')} server={inst.get('server_status')} "
              f"ip={ip}", flush=True)
        if inst.get("status") == "active" and ip and ip != "0.0.0.0":
            break
        time.sleep(15)
    print("  waiting for SSH and the sandbox image build (several minutes)…", flush=True)
    while time.time() < deadline:
        subprocess.run(f"ssh-keyscan -T 5 {ip} >> ~/.ssh/known_hosts 2>/dev/null", shell=True)
        ok = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"root@{ip}", "test", "-f",
                             READY_MARKER], capture_output=True).returncode == 0
        if ok:
            return ip
        time.sleep(20)
    raise SystemExit(f"sandbox VM {instance_id} not ready after {timeout_s}s (ip {ip}); check it in the Vultr portal")


def write_env(docker_host: str) -> None:
    env = ROOT / ".env"
    text = env.read_text() if env.exists() else ""
    line = f"SANDBOX_DOCKER_HOST={docker_host}"
    if re.search(r"^SANDBOX_DOCKER_HOST=.*$", text, re.M):
        text = re.sub(r"^SANDBOX_DOCKER_HOST=.*$", line, text, flags=re.M)
    else:
        text += f"\n{line}\n"
    env.write_text(text)
    print(f"  wrote {line} to {env}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create a sandbox VM and wait until it is ready")
    c.add_argument("--region", default=os.environ.get("SANDBOX_VM_REGION", "ams"))
    c.add_argument("--plan", default=os.environ.get("SANDBOX_VM_PLAN", "vc2-2c-4gb"))
    c.add_argument("--os", default="Ubuntu 24.04")
    c.add_argument("--no-wait", action="store_true")
    c.add_argument("--write-env", action="store_true", help="set SANDBOX_DOCKER_HOST in .env when ready")
    sub.add_parser("status", help="list sandbox VMs")
    d = sub.add_parser("destroy", help="destroy sandbox VMs")
    d.add_argument("instance_id", nargs="?")
    d.add_argument("--all", action="store_true")
    args = ap.parse_args()

    v = Vultr(api_key())
    if args.cmd == "status":
        for inst in v.list():
            print(f"{inst['id']}  {inst.get('main_ip')}  {inst.get('status')}/{inst.get('server_status')}  "
                  f"{inst.get('region')}  {inst.get('plan')}  {inst.get('label')}")
        return
    if args.cmd == "destroy":
        targets = [i["id"] for i in v.list()] if args.all else [args.instance_id] if args.instance_id else []
        if not targets:
            sys.exit("give an instance id or --all")
        for iid in targets:
            v.delete(iid)
            print(f"destroyed {iid}")
        print("Remember to clear SANDBOX_DOCKER_HOST in .env (or create a new VM) and restart the app.")
        return

    pubkey = ensure_ssh_key()
    label = f"rwr-sandbox-{time.strftime('%m%d-%H%M')}"
    inst = v.create(region=args.region, plan=args.plan, os_id=v.os_id(args.os), label=label,
                    user_data=cloud_init(pubkey, my_public_ip()))
    print(f"created {inst['id']} ({label}) in {args.region}, plan {args.plan}")
    if args.no_wait:
        return
    ip = wait_ready(v, inst["id"])
    docker_host = f"ssh://root@{ip}"
    print(f"\nready: SANDBOX_DOCKER_HOST={docker_host}")
    if args.write_env:
        write_env(docker_host)
    print("Restart the app (sudo systemctl restart rwr); the header should still say 'sandbox: docker'.")


if __name__ == "__main__":
    main()
