# Gallery Core 后的受限 managed VMM Core 发布

这是实际 Gallery `core_followup V1` 已提交后的单次功能升级入口。它不重放 Gallery、Assistant successor 或首次安装，不是等价 JAR 重打包，也不是任意无限 policy 升级器。它使用原 private owner/CODEOWNER review、正式 release artifacts、共享 lease、Core publisher、一致性 PostgreSQL/private-files backup 和原事务恢复。

本入口只接受 `R25_ASSISTANT_CORE_FOLLOWUP_V1` 为前像，生成 `R25_MANAGED_VMM_CORE_FOLLOWUP_V1`，绑定 schema 为 `R25_MANAGED_VMM_CORE_FOLLOWUP_BINDING_V1`。新的 Core 和五个 managed 配置值成对通过原 publisher 安装/恢复。旧 policy 保存至原内容寻址 `core-followup-baselines/<sha>.json`；旧 approval/journal/accepted record、AI 源码/CURRENT/配置不重写。AI provenance 继续核对其原始 successor 的 snapshot 与 Core，而不伪造它属于本次后续 Core。

## 唯一推荐顺序

1. 正式 Core release candidate + 本修复正常 main→release 后的正式 Deploy **同一次 package run** 的 25 文件 controller 包和 4 文件 VMM extension；PR CI/本地包不是正式产物。保持 Core publish flag 禁用，package 不能安装。
2. 在 VMM overlay **尚未安装** 时，准备并实际审批本 Core-only plan，绑定新的控制文件/policy。
3. 原 Core publisher 执行一致性 backup、V001–V016 精确链检查、JAR/application 成对安装、读验收、COMMITTED。managed 开关已 true，但 VMM 尚未运行，因此音乐派发失败闭合；Guide/Critic/Assistant 继续原配置。
4. 使用同一次新正式 Deploy 的 VMM extension，另行准备和审批 disabled overlay/role。它只接受新已批准 Core guard 与本 extension 中 guard **逐字一致**的 managed baseline，不允许任何自填“可接受旧 hash”。保持 role disabled、gate CLOSED。真正 startup/model loading/单次验收需要独立授权。

若 VMM overlay 已安装，本 Core binder 在停 watchdog 之前拒绝 `managed_vmm_overlay_requires_explicit_rollback`。不得隐式卸载、改 POLICY、删除 ACTIVE、混用旧包：先按该 overlay 的原批准 digest，通过原 disabled/stopped/no-inflight rollback 及 role 回退流程恢复其精确前像，再重新取证准备本 plan。该反向路径不是本命令自动执行的一部分。不能在开放 VMM 的同时把 Core 回退到 legacy 无门禁配置。

## 配置转换是发布事务的一部分

在已批准原 `application.yml` 字节末尾，只追加五个固定点式属性，不合并 `.env`，不改其他条目：

| 键 | 限制 |
| --- | --- |
| `auralink.vmm-managed.enabled` | JSON/YAML boolean `true`，启用失败闭合门禁；不是开启服务 |
| `auralink.vmm-managed.token-file` | 固定 `/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z/runtime/config/vmm-token`；只引用，不包含或生成 token |
| `auralink.vmm-managed.acceptance-owner-id` | 经独立核对的可信 account 数字 ID，正整数 |
| `auralink.vmm-managed.acceptance-source-painting-id` | 真实 gallery projection 的内部数字 ID，正整数；不是浏览器指定 owner 或演示 fixture ID |
| `auralink.vmm-managed.acceptance-duration-seconds` | 整数 3–30 |

五键来自私有 request，全部进入 plan/policy/config 摘要；token 值绝不进入报告/仓库。既有 managed 别名、未知字段、boolean 冒充 ID、任意 token 路径、越界时长均拒绝。token 未提供、listener 不在或服务 CLOSED 时 Core 仍启动，只是音乐不可用。后续 private token 配置须按既有私有 owner/mode 流程独立完成；本 binder 不写 credential。

发布前与实际 Java identity 检查拒绝未绑定的 VMM 环境变量/CLI 覆盖，包括 Spring relaxed 名称 `AURALINK_VMMMANAGED_ENABLED`。现有生成 5s connect/600s read、3–30 秒/default10、模型参数与 retry 策略不变。配置转换不需要新 migration。

## 实际支持的准备与执行入口

操作者从真实正式 manifest/incoming 和现场只读证据准备私有
`runtime/core-publications/MANAGED_VMM_CORE_REQUEST.json`（目录0700/文件0600）。它保持原 request 的 `deploy,core,web_commit,evidence,compatibility,compatibility_evidence_sha256,review_evidence_sha256`，schema 改为 `R25_MANAGED_VMM_CORE_FOLLOWUP_REQUEST_V1`，另加 `managed_vmm` 五个短键（`enabled,token-file,acceptance-owner-id,acceptance-source-painting-id,acceptance-duration-seconds`）。`web_commit` 为现有已审核兼容 Web source；本次不发布 Web。

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3
: "${DEPLOY_RELEASE_SHA:?actual newly reviewed formal release SHA}"
BUNDLE="$R/operations/r25-controller-candidates/$DEPLOY_RELEASE_SHA"
REQUEST_SHA256="$(sha256sum "$R/runtime/core-publications/MANAGED_VMM_CORE_REQUEST.json" | cut -d ' ' -f1)"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" prepare-managed-vmm-core --request-sha256 "$REQUEST_SHA256"
```

准备仅生成 unsigned exact plan 和 sidecars，不能替代真实批准。完整 plan 摘要批准后，按既有私有方式保存原字节为 `CONTROL_UPGRADE_APPROVAL.json`；不能自造 review 或把 unsigned 改名当批准。

```bash
: "${APPROVAL_SHA256:?actual separately approved exact plan digest}"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" preflight-managed-vmm-core --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" bind-managed-vmm-core --approval-sha256 "$APPROVAL_SHA256"
: "${CORE_SHA:?actual formal Core release SHA}"
: "${CORE_RUN:?actual formal run ID}"
: "${CORE_ATTEMPT:?actual formal run attempt}"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" stage --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" publish --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" status
```

绑定只暂停/恢复 watchdog，保持 Java/Guide/Critic/Next PID/ticks；Core publication 按原事务暂停 Java/watchdog，备份 DB 与 private attachments、检查 forward schema、安装并验收，停机必须实测。禁止绕过共享锁、备份、保护或另起 publisher。

## 进行中的任务与未知结果

原全部 Creation/Guide/Critic/Assistant idle 门槛保留，音乐聚合不是替代。新增 `core_acceptance.MANAGED_VMM_INFLIGHT_SQL` 仅返回 13 个匿名统计字段：三个分组及十个 queued/running、当前/全部历史 SEND_STARTED、未结束 attempts、explicit uncertainty 计数。SQL 使用 `BEGIN READ ONLY`/8秒 statement timeout/`ROLLBACK`，仍通过原身份绑定、只读环境、sealed psql 执行；调用方不能提交任意 SQL。

prepare/preflight/bind 在 pause 前运行聚合。十个风险计数任一非零即拒绝并返回安全 aggregate；不自动清零，不忽略较旧 attempts，也不把 `SEND_STARTED` 当作已证实发生生成。没有监听器或当前空闲不证明历史未知已消除。需就具体记录另行只读核对，再通过正常批准流程处理。

本 policy 已安装后，后续 VMM activation 前可重查：

```bash
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" inspect-managed-vmm-work --approval-sha256 "$APPROVAL_SHA256"
```

此命令没有 SQL 参数、没有生成请求；输出 aggregate 后，风险计数非零退出65。读到零不是未来并发保证；VMM CLOSED、私有 Core gate、一次性 permit 的最终原子准入仍需保留。任何未知调用不得自动重派。

## 验收与恢复

新 candidate 专用读验收保留 Gallery/Knowledge/Assistant，并增加一个 authenticated `GET /api/v1/workflow/node-types`：PAINTING_TO_MUSIC 必须为 PAINTING→AUDIO 且 operation/providers 均不可执行；不能用“整个 creations feature 关闭”伪装 managed gate。该 CLOSED 要求不套在旧 Core baseline/rollback 验收上。未来 VMM ACCEPTANCE/OPEN 时，下一次 Core publication/verify-current 必须先原入口 close gate；不为方便后续开放而取消本次闭合验收。

```bash
# controller 原事务未开始：NOT_STARTED；部分或不确定：恢复同一 digest。
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" recover-managed-vmm-core --approval-sha256 "$APPROVAL_SHA256"
# 只在实际存在的原 Core publication transaction 上使用：
: "${CORE_TX:?actual original Core transaction ID}"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" recover --transaction "$CORE_TX"
# 仅 COMMITTED 且已确认角色关闭、兼容回退获批时：
"$PY" -B "$R/operations/r25-publisher/core_publish.py" rollback --transaction "$CORE_TX"
```

controller 部分复制只能CAS恢复原控制文件/policy/handoff；第三方漂移拒绝。旧 accepted record 保留原 policy SHA。Core recover/rollback 保留已前进 V001–V016、当前 DB/private files 和删除墓碑，恢复原 JAR+配置对，不恢复旧全库、不重派音乐。VMM overlay 若已后装，必须先处理其依赖并关闭/disabled/stopped/回退后，才能恢复更早 Core controller policy；不能强行编辑绑定。

离线验证使用真实临时文件、flock/fsync/CAS、原 prepare/bind/recover 和实际 guard/config/acceptance 方法；OS supervision/正式GitHub批准/PG调用在controller fixture内明确模拟。独立实际 PostgreSQL 聚合验证由阶段证据补充。它不证明生产 GPU、模型加载或真实音乐成功。
