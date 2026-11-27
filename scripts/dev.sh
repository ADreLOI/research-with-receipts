#!/usr/bin/env bash
# Run the app locally with auto-reload. Uses .env if present.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -d .venv ]]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements-dev.txt
exec uvicorn app.main:app --reload --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
