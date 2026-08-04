#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
    echo "[失败] 缺少 .env，请先执行：cp .env.example .env"
    exit 1
fi

docker compose \
    --env-file .env \
    -f docker-compose.yml \
    -f docker-compose.gpu.yml \
    up -d
