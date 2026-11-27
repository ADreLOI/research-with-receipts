#!/usr/bin/env bash
# One-shot setup for a fresh Vultr Cloud Compute VM (Ubuntu 24.04).
#   ssh root@<vm-ip>
#   git clone <repo-url> /opt/rwr && cd /opt/rwr && sudo bash deploy/setup_vultr_vm.sh [domain]
# Afterwards edit /opt/rwr/.env (add VULTR_INFERENCE_API_KEY) and run:
#   systemctl restart rwr
set -euo pipefail
# iptables-persistent and friends must not stop at interactive debconf prompts.
export DEBIAN_FRONTEND=noninteractive
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${1:-}"

echo "==> packages"
apt-get update -y
apt-get install -y ca-certificates curl git python3-venv python3-pip ufw iptables-persistent

echo "==> docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

if [[ "${INSTALL_GVISOR:-0}" == "1" ]]; then
  echo "==> gVisor (runsc)"
  curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
    > /etc/apt/sources.list.d/gvisor.list
  apt-get update -y && apt-get install -y runsc
  runsc install && systemctl restart docker
fi

echo "==> network guard: sandboxes may not reach metadata or private ranges"
for net in 169.254.0.0/16 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
  iptables -C DOCKER-USER -d "$net" -m conntrack --ctstate NEW -j DROP 2>/dev/null \
    || iptables -I DOCKER-USER -d "$net" -m conntrack --ctstate NEW -j DROP
done
# Allow replies for established connections and the default bridge's own gateway DNS.
iptables -C DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN 2>/dev/null \
  || iptables -I DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
netfilter-persistent save || true

echo "==> app user and venv"
id rwr >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin rwr
usermod -aG docker rwr
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

echo "==> firewall"
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

echo
echo "Done. App: $BASE"
echo "Next: put VULTR_INFERENCE_API_KEY in $APP_DIR/.env, then: systemctl restart rwr"
