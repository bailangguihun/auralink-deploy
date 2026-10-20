# ArtLIVE 画智体 · Deployment

ArtLIVE 多仓库系统的部署配置与发布控制中心。该仓库维护 R25 协调发布契约、控制器、验收与回滚测试，同时保留旧版 Auralink Docker Compose 模板。

> 这里不存放业务源码、模型权重、生产数据库、画作资源、生成文件或真实密钥。

## 仓库职责

| 范围 | 当前用途 |
| --- | --- |
| R25 发布控制 | 绑定组件版本、生成发布制品、验证控制器事务与恢复路径 |
| Core API 发布契约 | 定义 Core 候选、验收、发布和回滚边界 |
| AI 发布契约 | 定义 Guide/Critic 候选、运行配置与发布边界 |
| 配置验证 | 校验 Shell、Compose 与 R25 Python 控制器 |
| 旧 Compose 模板 | 保留 Web、Core API、VMM、Vision 与 Nginx 的 legacy 配置 |

应用源码分别位于：

- [auralink-web](https://github.com/bailangguihun/auralink-web)
- [auralink-core-api](https://github.com/bailangguihun/auralink-core-api)
- [auralink-ai-service](https://github.com/bailangguihun/auralink-ai-service)

## 重要边界

当前 R25 生产运行方式与仓库中的旧 Compose 模板不同：

- R25 生产包含 Web、Core API、Guide 与 Critic，并使用独立的受控发布流程。
- `docker-compose.yml` 是旧版模板，Core API 使用 SQLite，并保留 VMM/Vision 配置。
- 所有旧服务均要求显式启用 `legacy` profile；默认配置不会启动它们。
- 推送 `release` 只会测试并打包 R25 控制器制品，结果标记为 `NOT_DEPLOYED`。
- GitHub Actions 不连接生产服务器，也不会安装控制器、切换进程或迁移数据库。

完整边界见 [系统架构](docs/ARCHITECTURE.md)、[协作说明](docs/collaboration.md) 与 [R25 运行说明](docs/r25-runtime.md)。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `r25/` | 发布候选、验收、发布、回滚控制器及故障恢复测试 |
| `docs/` | 架构、协作、运行与组件发布说明 |
| `scripts/` | 配置验证及旧 Compose 辅助脚本 |
| `nginx/` | 旧模板的反向代理配置 |
| `docker-compose.yml` | CPU/基础 legacy Compose 模板 |
| `docker-compose.gpu.yml` | GPU 覆盖配置 |
| `.env.example` | 无密钥的示例环境配置 |

## 环境要求

仅验证仓库配置时需要：

- Bash
- Python 3
- Docker CLI
- Docker Compose plugin

运行旧 GPU 模板还需要 NVIDIA Container Toolkit、可用模型文件及对应容器镜像。

## 配置

复制示例配置：

```bash
cp .env.example .env
```

编辑 `.env` 并替换占位值。真实 `.env` 不得提交到 Git。

主要配置包括：

- Web、Core API 与 AI Service 镜像
- 对外 HTTP 端口与 JWT 密钥
- 数据库、上传、音频、画作及模型的持久化目录
- 官方画作 CSV 路径
- VMM 与 Vision 模型路径和 GPU 设置

`.env.example` 中的镜像、目录与端口只是 legacy 模板，不代表生产服务器的实际配置。

## 本地验证

推荐在提交前执行强制检查：

```bash
set -Eeuo pipefail

while IFS= read -r -d '' script; do
  bash -n "$script"
done < <(find scripts -type f -name '*.sh' -print0)

temporary_dir="$(mktemp -d)"
trap 'rm -f "$temporary_dir/.env"; rmdir "$temporary_dir"' EXIT
cp .env.example "$temporary_dir/.env"

docker compose --env-file "$temporary_dir/.env" \
  --profile legacy -f docker-compose.yml config --quiet

docker compose --env-file "$temporary_dir/.env" \
  --profile legacy -f docker-compose.yml -f docker-compose.gpu.yml config --quiet

python -m unittest discover -s r25 -p 'test_*.py' -v
```

也可运行快速验证脚本：

```bash
./scripts/validate.sh
```

若工具缺失，快速脚本可能跳过部分检查；CI 使用上面的强制检查，不会把“跳过”视为通过。

## 旧 Compose 模板

只有明确维护旧音乐模板时才执行：

```bash
./scripts/start.sh --legacy
./scripts/status.sh
./scripts/stop.sh
```

安全约束：

- `./scripts/start.sh` 不带 `--legacy` 会在调用 Docker 前拒绝执行。
- `docker compose up` 默认没有服务。
- 显式选择 legacy 服务、使用 `--profile legacy` 或设置 `COMPOSE_PROFILES=legacy` 才表示选择旧模板。
- 示例镜像名是占位配置，实际镜像必须由相应应用仓库发布。

## R25 发布制品

向 `release` 推送通过审核的变更后，Actions 会生成：

```text
r25-controller-<sha>-<run>-<attempt>
```

制品包含固定控制器 ZIP 和文件清单，用于后续受控安装。制品生成成功仅证明控制器经过测试与打包，不表示已部署到生产。

发布契约：

- [Core API Release Contract](r25/CORE_RELEASE_CONTRACT.md)
- [AI Release Contract](r25/AI_RELEASE_CONTRACT.md)

生产数据库、媒体、模型文件、环境包装器与凭据始终保留在服务器侧，并由单独授权的运维流程管理。

## 分支与协作

- 日常修改：功能分支 → `main` Pull Request
- 阶段发布：`main` → `release` Pull Request
- 跨仓库变更应在 PR 中关联依赖仓库与合并顺序
- CI 通过只代表配置与事务测试通过，不代表生产部署完成

详情见 [贡献说明](CONTRIBUTING.md)。
