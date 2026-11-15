# Community schema 3 代码发布

此后继用于已完成 Reference 迁移的原 R25 安装。Java Core 的实际 CURRENT、COMMITTED 事务和运行 JAR 与 Community 新包源码来源分别绑定；只更新社区 Node 代码，不执行 SQL 迁移。Web 随后通过原发布窗口部署。

## 固定边界

控制器批准 `R25_COMMUNITY_CODE_CONTROLLER_V1` 仅替换原 `vmm_controller.py`、`lifecycle_adapter.py` 和 handoff 中两项控制摘要。保留 Core policy/CURRENT、所有原批准与指针、数据库、应用配置、原锁和租约。旧 Reference 证明链完整执行；历史投影仅包含上述三个固定控制目标的已核验前像，不能替代真实 Core、policy、批准或进程记录。新 lifecycle 将原 watchdog 路由至正式新 Community 控制包，并将完整验证/启动调用超时从 20 秒提高为 90 秒。

应用批准 `ARTLIVE_COMMUNITY_CODE_PUBLICATION_V1` 绑定：

- 现用 Reference final 的批准、COMMITTED journal、指针、配置、监督与进程回执。
- 原 Java Core CURRENT、COMMITTED 事务和运行 JAR 身份。
- 新 Community 包的 release commit、Actions run/attempt、archive 与 manifest 摘要。
- 新鲜 schema `[1,2,3]`、原迁移回执及当前数据指纹；当前正常业务变化不能用最初迁移前像判作丢失。

本版本只接受已确认终止的原 final 进程。若最后监督决定缺失进程回执及日志，可在准确批准后对它做独立退役处置。必须先用原入口停止 watchdog、取得原租约、确认无监听器、匹配 Node 或遗留控制器；无法核实的相关进程一律拒绝。旧账本、尝试、预算及回执均不改写，也不补造 EXITED。新进程使用新 UUID，并将旧 attempts 和 budget_start 保留到独立监督状态中。

## 控制器安装

先通过 Deploy 保护分支审核与 release 构建，取得同一次成功正式任务的 R25/VMM/Community 控制制品，核验 Git 源码、外层制品及内层文件清单后仅暂存到固定 inactive candidate 目录。不能用 PR 包或本地包代替正式制品。

在 `runtime/vmm-controller/community-code-inputs/<REQUEST_SHA>/` 分别存放精确 request.json 与实际技术复核 review.json。请求 schema 为 `R25_COMMUNITY_CODE_CONTROLLER_REQUEST_V1`，字段为 `deploy`、`controller_deploy`、`community_deploy`、`community_source_provenance`、`review_evidence_sha256`。三个 deploy 字段沿用现有正式清单合同；source 字段明确包含 `commit/run_id/run_attempt/archive_sha256/manifest_sha256`。

使用正式候选路径中的 vmm_controller.py：

```text
prepare-community-code --request-sha256 REQUEST_SHA
preflight-community-code --approval-sha256 PLAN_SHA
install-community-code --approval-sha256 PLAN_SHA
recover-community-code --approval-sha256 PLAN_SHA
```

prepare 只产生 UNSIGNED_PLAN.json，不能自行充当批准。准确计划需实际复核后原字节另存 approval.json。安装器使用原 supervisor stop/start、原共享租约及 CAS，保存私有 before/after、独立 journal 与 `COMMUNITY_CODE_PUBLICATION_ACTIVE.json`。三个控制文件任一中断时保持 watchdog 停止，使用同一计划显式 forward recovery；禁止重建计划掩盖 mixed 状态。原控制文件尚未改变的失败才恢复旧 watchdog。

## 社区包发布

在新的正式 Community 控制 bundle 中使用 community_host_cli.py，保留原 `--plan/--plan-sha256`，传入新 `--bundle-manifest-sha256`，及原 Knowledge/Recovery/Studio/Reference 绑定，再加 `--code-publication-sha256 CODE_CONTROLLER_PLAN_SHA`。新绑定明确验证旧链，不直接替换旧批准中的 controller digest。

```text
code-prepare --archive ZIP --manifest MANIFEST --review-sha256 ACTUAL_REVIEW_SHA
code-install --package-plan EXACT_PLAN --package-plan-sha256 PLAN_SHA --archive ZIP --manifest MANIFEST
code-recover --package-plan EXACT_PLAN --package-plan-sha256 PLAN_SHA --archive ZIP --manifest MANIFEST
```

code-prepare 产生独立 code-upgrades 计划；准确计划另行复核并批准。发布前在原托管入口停妥 watchdog，安装后验证真实归属、健康、`CODE_ACTIVE.json` 和 COMMITTED journal，再恢复原 watchdog。源码、配置和媒体沿用原私有权限；Node 不继承部署 FD。迁移回执只读检查，绝不调用 migrate/restore。

状态顺序为 PREPARED → MATERIALIZED → RETIRED → START_INTENT → STARTED → ACTIVATE_INTENT → COMMITTED。每次从 RETIRED 恢复到新 START_INTENT 前重新检查静默现场和原证据。独立 decision-retirement.json 必须持续存在且准确匹配计划，原父监督账本也持续校验。

START_INTENT 后失回只允许观察准确 UUID 的已有归属回执；没有回执时拒绝再次 spawn。候选失败不会自动回交原本处于未知状态的旧服务，也不会退款或清空预算；保留现场，另行准确审查处置。不得把“保留停止状态”报告为已恢复旧服务。

## 验证边界

test_community_code_package.py 使用真实隔离 Node、原 flock、完整包校验和显式内存数据库夹具；覆盖旧决定退役、预算保留、各阶段失回、新 spawn 不重放、无监听遗留进程、处置/父账本漂移。test_community_code_controller.py 执行完整保留 Reference 证明、正式格式制品、真实文件 CAS 和三目标中断恢复，Java/SQL/watchdog 采用既有显式夹具。生产安装后仍需独立核对实际制品、schema/回执、媒体、版本、进程和健康。

本次应用不涉及 TURN 配置。真实 Edge 平板笔触、跨网络语音和持续通话仍需实机验收，不能由夹具或健康检查外推。
