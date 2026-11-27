#!/usr/bin/env bash
# One-shot setup for a fresh Vultr Cloud Compute VM (Ubuntu 24.04).
#   ssh root@<vm-ip>
#   git clone <repo-url> /opt/rwr && cd /opt/rwr && sudo bash deploy/setup_vultr_vm.sh [domain]
# Afterwards edit /opt/rwr/.env (add VULTR_INFERENCE_API_KEY) and run:
#   systemctl restart rwr
set -euo pipefail
# Never stop at interactive debconf prompts.
export DEBIAN_FRONTEND=noninteractive
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${1:-}"

echo "==> packages"
apt-get update -y
# No iptables-persistent: on 24.04 it conflicts with ufw. The egress guard is a systemd unit instead.
apt-get install -y ca-certificates curl git python3-venv python3-pip ufw

echo "==> docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
# Containers resolve names through public resolvers, so DNS keeps working even
# though the egress guard below drops private and link-local destinations.
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("/etc/docker/daemon.json")
cfg = json.loads(p.read_text() or "{}") if p.exists() else {}
cfg.setdefault("dns", ["1.1.1.1", "8.8.8.8"])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(cfg, indent=2) + "\n")
PY
systemctl enable docker && systemctl restart docker

if [[ "${INSTALL_GVISOR:-0}" == "1" ]]; then
  echo "==> gVisor (runsc)"
  curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
    > /etc/apt/sources.list.d/gvisor.list
  apt-get update -y && apt-get install -y runsc
  runsc install && systemctl restart docker
fi

echo "==> network guard: sandboxes may not reach metadata or private ranges"
# Applied now and again on every boot / Docker restart by a small systemd unit.
# Root runs it, so install a root-owned copy outside the app checkout.
install -m 0755 -o root -g root "$APP_DIR/deploy/egress_guard.sh" /usr/local/sbin/rwr-egress-guard
install -m 0644 -o root -g root "$APP_DIR/deploy/rwr-egress-guard.service" /etc/systemd/system/rwr-egress-guard.service
systemctl daemon-reload
systemctl enable rwr-egress-guard
systemctl restart rwr-egress-guard

echo "==> app user and venv"
id rwr >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin rwr
usermod -aG docker rwr
# The service sees its home read-only (ProtectHome); give the docker CLI its config dir up front.
install -d -o rwr -g rwr -m 700 "$(getent passwd rwr | cut -d: -f6)/.docker"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
[[ -f "$APP_DIR/.env" ]] || cp "$APP_DIR/.env.example" "$APP_DIR/.env"
PUBLIC_IP="$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"
if [[ -n "$DOMAIN" ]]; then BASE="https://$DOMAIN"; else BASE="http://$PUBLIC_IP"; fi
sed -i "s#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=$BASE#" "$APP_DIR/.env"
sed -i "s#^SANDBOX_BACKEND=.*#SANDBOX_BACKEND=docker#" "$APP_DIR/.env"
sed -i "s#^DATA_DIR=.*#DATA_DIR=$APP_DIR/data#" "$APP_DIR/.env"
mkdir -p "$APP_DIR/data"
chown -R rwr:rwr "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "==> sandbox image"
docker build -t rwr-sandbox:latest "$APP_DIR/sandbox_image"

echo "==> systemd"
sed "s#__APP_DIR__#$APP_DIR#g" "$APP_DIR/deploy/rwr.service" > /etc/systemd/system/rwr.service
systemctl daemon-reload
systemctl enable --now rwr

echo "==> firewall"
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

echo "==> caddy (reverse proxy, automatic HTTPS when a domain is given)"
if ! command -v caddy >/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi
SITE="${DOMAIN:-:80}"
sed "s#__SITE__#$SITE#g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy


echo
echo "Done. App: $BASE"
echo "Next: put VULTR_INFERENCE_API_KEY in $APP_DIR/.env, then: systemctl restart rwr"
