#!/usr/bin/env bash
# Update an existing deployment to the latest commit of its branch, as root:
#   cd /opt/rwr && sudo bash deploy/update.sh
# Local edits to tracked files are saved to /root/rwr-local-changes-<time>.patch
# and dropped, demo/cassettes is backed up to /root/rwr-cassettes-<time>.tgz
# first (.env and data/ are not tracked and stay as they are).
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

stamp="$(date +%H%M%S)"
# Recordings made on this VM live in demo/cassettes; keep a copy before the reset.
tar czf "/root/rwr-cassettes-$stamp.tgz" demo/cassettes
echo "==> demo/cassettes backed up to /root/rwr-cassettes-$stamp.tgz"
if ! git diff --quiet || [[ -n "$(git ls-files --others --exclude-standard demo/cassettes)" ]]; then
  patch="/root/rwr-local-changes-$stamp.patch"
  git diff > "$patch"
  echo "==> local edits saved to $patch and reverted"
  git checkout -- .
  git clean -fdq demo/cassettes
fi
branch="$(git rev-parse --abbrev-ref HEAD)"
before="$(git rev-parse --short HEAD)"
git pull --ff-only origin "$branch"
echo "==> $before -> $(git rev-parse --short HEAD)"

echo "==> egress guard unit"
install -m 0755 -o root -g root deploy/egress_guard.sh /usr/local/sbin/rwr-egress-guard
install -m 0644 -o root -g root deploy/rwr-egress-guard.service /etc/systemd/system/rwr-egress-guard.service
systemctl daemon-reload
systemctl enable rwr-egress-guard >/dev/null 2>&1
systemctl restart rwr-egress-guard

echo "==> python deps and sandbox image"
"$APP_DIR/.venv/bin/pip" install -q -r requirements.txt
docker build -q -t rwr-sandbox:latest sandbox_image
chown -R rwr:rwr "$APP_DIR"

echo "==> restart"
systemctl restart rwr
sleep 3
sudo -u rwr "$APP_DIR/.venv/bin/python" scripts/doctor.py --quick || true
echo "Full check, including the sandbox tools: sudo -u rwr $APP_DIR/.venv/bin/python scripts/doctor.py"
