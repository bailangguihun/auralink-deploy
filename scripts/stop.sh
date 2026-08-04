#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

docker compose \
    --env-file .env \
    -f docker-compose.yml \
    -f docker-compose.gpu.yml \
    down
