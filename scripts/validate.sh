#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if command -v docker >/dev/null 2>&1 &&
   docker compose version >/dev/null 2>&1; then
    docker compose \
        --env-file .env.example \
        -f docker-compose.yml \
        config >/dev/null

    echo "[通过] Docker Compose 配置有效"
else
    python3 - <<'PY'
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[跳过] Docker Compose 不可用，且未安装 PyYAML")
    raise SystemExit(0)

for filename in (
    "docker-compose.yml",
    "docker-compose.gpu.yml",
):
    path = Path(filename)
    with path.open("r", encoding="utf-8") as file:
        yaml.safe_load(file)
    print(f"[通过] {filename} YAML 解析成功")
PY
fi

if command -v nginx >/dev/null 2>&1; then
    echo "[信息] 当前检查不替换系统 Nginx 配置，跳过 nginx -t"
fi
