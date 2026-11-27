#!/usr/bin/env bash
# Build the sandbox image (Chromium + Playwright + Python toolbox).
# With SANDBOX_DOCKER_HOST set, the image is built on the remote sandbox VM.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${SANDBOX_IMAGE:-rwr-sandbox:latest}"
if [[ -n "${SANDBOX_DOCKER_HOST:-}" ]]; then export DOCKER_HOST="$SANDBOX_DOCKER_HOST"; fi
docker build -t "$IMAGE" sandbox_image
echo "Built $IMAGE"
