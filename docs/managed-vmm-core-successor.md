# 已安装 disabled VMM overlay 的有限 Core 后继发布

本入口解决已提交 managed Core、已安装 VMM overlay 和完整 disabled role 后，不能绑定新的功能 Core JAR 的兼容性缺口。它不重放首次安装、不卸载 overlay、不修改旧批准，也不是等价重打包或任意无限升级框架。唯一前像为 `R25_MANAGED_VMM_CORE_FOLLOWUP_V1`，后像为 `R25_MANAGED_VMM_CORE_SUCCESSOR_V1`；本次后继不能再次作为该入口的前像。

## 保持不变的边界

- 应用配置字节完全不变，包括五个 managed VMM 配置键、Assistant、Guide/Critic、PostgreSQL、媒体路径；不生成或轮换 token。
- 原 overlay `ACTIVE.json`、role `ROLE_ACTIVE.json`、原批准/journal、role/service/token、lifecycle/service 包装器、模型文件及权限保持不变。绑定时 role 必须 disabled、desired STOPPED、process null、gate CLOSED，原请求账本字节必须匹配。
- Core/AI/Web 运行身份、AI CURRENT/配置/历史 provenance、c5ea owner 与原 frontend selection 均独立验证。Core binder 只协调 watchdog，不能重启 Java/AI/Next；稍后的 Core publisher 才进行一致性备份和 Java/watchdog 暂停。
- 原共享 lease 和 CAS 保留；不删除锁、不杀未知持有者。任何排队/运行/未结束/UNKNOWN 音乐 work 聚合非零，绑定前拒绝；没有监听器不是消除历史不确定性的证据。

## 正式来源和增量证明

必须先正常 review、main→release promotion，再取得同一 Deploy release SHA、run ID、attempt 的 **25 文件 controller 包和 4 文件 VMM extension**。外层 GitHub ZIP digest、内层 archive/manifest/逐文件 hash 分层验证。两份 `core_release_guard.py` 必须逐字相同。`vmm_lifecycle.py`、`vmm_service.py` 必须与已安装 overlay 完全相同；只更新有限 Core 控制文件和 `vmm_controller.py`。不改变固定包 inventory。

Core 必须为真正 release-bound 的正式 candidate，不可使用 PR/main CI 或本地包替代。其 manifest、JAR、verification report、release/run/attempt 由现有 verifier 验证；源码 inventory 另由正式工作流证据独立绑定，不宣称此入口校验全部源码。功能 payload 必须不同。每个旧控制文件、policy/handoff、Core/AI accepted record 都进入精确前像。

原 `POLICY.json` 和批准按原字节 SHA 保留。原 overlay 不重签；新增固定 `runtime/vmm-controller/CORE_SUCCESSOR_ACTIVE.json`，只引用本次新 Core controller approval。加载器验证该批准、review、原 overlay/role 证明、所有后像和 `VERIFY_INTENT`/`INSTALLED` journal 后，才提供新的有效 guard/hash。

绑定时冻结 lifecycle/gate 字节；运行时由原 lifecycle/gate 自身的授权检查管理合法变化，不把状态永久冻结。此入口保留现有 role 的生命周期，**不**扩大旧 `prepare-role` 或 overlay rollback 为任意后继重绑入口。

## 准备命令（只生成 unsigned plan）

按原可信 staging 契约放置 Core incoming、controller 及同 SHA extension。此处变量必须从已核实的正式制品填写，下面不是最终 artifact/批准身份：

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3
: "${DEPLOY_RELEASE_SHA:?reviewed formal release SHA}"
BUNDLE="$R/operations/r25-controller-candidates/$DEPLOY_RELEASE_SHA"
# extension 固定位置：$R/operations/r25-vmm-candidates/$DEPLOY_RELEASE_SHA
REQUEST_SHA256="$(sha256sum "$R/runtime/core-publications/MANAGED_VMM_SUCCESSOR_REQUEST.json" | cut -d ' ' -f1)"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" prepare-managed-vmm-successor --request-sha256 "$REQUEST_SHA256"
```

私有 request 目录0700/文件0600，schema 为 `R25_MANAGED_VMM_CORE_SUCCESSOR_REQUEST_V1`。字段严格为 `deploy,core,web_commit,evidence,compatibility,compatibility_evidence_sha256,review_evidence_sha256,vmm_deploy`：

- `deploy` 为现有正式 controller identity：repository/ref/commit/run_id/run_attempt/archive_sha256/manifest_sha256/tool_sha256。
- `vmm_deploy` 为同一次 extension identity：repository/ref/commit/run_id/run_attempt/archive_sha256/manifest_sha256/tool_sha256，其中 tool 为该 extension 的 `vmm_controller.py`。不能混用旧 release 或其他 run。
- `core` 为真实 commit/run_id/run_attempt；`web_commit` 保留现有 policy 中兼容 Web binding，不发布 Web。
- evidence/compatibility/review 使用既有契约及真实审查证据，不能构造审批。无 managed 配置输入；全部保留已绑定配置。

prepare 在已有共享 lease 下读取 current/accepted/overlay/role、Core candidate、只读 work 风险和应用 PID/ticks，生成 `core-followup-plans/<request-sha>/UNSIGNED_PLAN.json` 及 sidecars。若 watchdog 长持 lease，只按现有有界协调流程暂时暂停/恢复 watchdog；保持应用运行，不边等审批边暂停监督。准备结果不等于批准，最终计划摘要只能由实际生成的字节得出。

## 精确批准后的绑定与发布

由既有私有批准过程把获批原字节保存为 `CONTROL_UPGRADE_APPROVAL.json`。执行前重新核对所有身份和 CLOSED 状态：

```bash
: "${APPROVAL_SHA256:?actual separately approved unsigned-plan digest}"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" preflight-managed-vmm-successor --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" bind-managed-vmm-successor --approval-sha256 "$APPROVAL_SHA256"
: "${CORE_SHA:?formal release SHA}"
: "${CORE_RUN:?formal run ID}"
: "${CORE_ATTEMPT:?formal attempt}"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" stage --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" publish --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" status
```

绑定先留存旧 `CONTROL_UPGRADE_ACTIVE.json` 原字节和全部 before images；复制后进入 `VERIFY_INTENT`，加载真实 policy/overlay 证明链并恢复 watchdog，最后 INSTALLED。Core publisher 仍使用原一致性 DB/private-files backup、V001–V016 checksums、JAR/config 对和读验收。配置为同字节并不跳过其身份验证；不恢复旧数据库或改变 migration。新 Core 的 node-types 必须确认 managed 音乐不可派发；不调用生成。

停机界限：绑定只暂停 watchdog；publication 暂停 Java/watchdog 并备份。实际持续时间须当次测量，不能把 fixture 时间当生产承诺。恢复后通过独立连接检查 watchdog、Core/AI/Next、认证只读 API、media 和 VMM disabled/stopped/CLOSED。

## 失败、回包丢失及恢复

仅 controller 绑定未完成、尚未开始 Core publication 时：

```bash
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" recover-managed-vmm-successor --approval-sha256 "$APPROVAL_SHA256"
```

若绑定 INSTALLED 且已开始 Core publication，保持新控制器，首先处理实际原 publisher 事务；以下不是与上面的连续执行脚本：

```bash
: "${CORE_TX:?actual original publication transaction ID}"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" recover --transaction "$CORE_TX"
```

仅 publication 已 COMMITTED、确认兼容恢复且另行获准时，使用应用 rollback，不能把 recover 当作已提交事务回退：

```bash
"$PY" -B "$R/operations/r25-publisher/core_publish.py" rollback --transaction "$CORE_TX"
```

未开始返回 NOT_STARTED。INSTALLED 回包丢失只核验并返回 NO_RECOVERY/NOOP；不另起绑定。部分/不确定状态按同一批准 digest CAS 恢复文件、原控制指针和仅属于本次计划的 marker，再校验旧运行身份并恢复监督；第三方漂移拒绝。不要手删 marker/锁。该 binder 没有回退已 INSTALLED 控制器的入口；成功绑定后即使应用回退也保持受验证的新控制器。若未来必须撤销已安装控制器，需另外审查的兼容路径，不能先让旧控制器面对新 JAR。原 overlay、role 和历史批准保留，不用旧 overlay rollback 绕过 successor。

## 验证与明确未验证项

回归使用真实临时文件、固定 manifest/inventory、flock、fsync/CAS，以及实际 prepare/bind/verify/recover/overlay 代码；覆盖错 run/hash/config、非关闭状态、未知 work、部分复制和提交回包丢失。监督进程、数据库查询和 Java fixture 边界显式模拟。现有 publisher 失败恢复套件继续运行。

上述证据不证明正式 GitHub review、生产安装、GPU readiness 或音乐成功。本次不消耗启动/生成许可，不重试历史失败 creation。后续新的单次 CLOSED startup、READY 后单次音乐批准、UNKNOWN 消耗及清理必须继续遵守独立授权；OPEN 不在本变更范围。
