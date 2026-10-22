# 已安装 VMM 的 V2 验收模块修订

该入口只用于已经提交 managed Core successor、已安装 overlay 和完整 disabled role 的一次模块修订。它修复验收窗口身份，不能重放首次安装或 Core 发布，也不启用 VMM、加载模型、提交业务请求或开放 OPEN。

## 身份与写入边界

要求原 `CORE_SUCCESSOR_ACTIVE.json` 对应真实 `R25_MANAGED_VMM_CORE_SUCCESSOR_BINDING_V1`、journal INSTALLED 和现存 policy/overlay/role 完整证明。原 ACTIVE、ROLE_ACTIVE、Core successor、批准、journal、Core/AI CURRENT、应用配置、服务绑定、token、媒体、模型与权限不变。

新输入为同一正式 release/run/attempt 的 controller 和 VMM extension。固定 25+4 文件库存不变；验证完整 manifest、archive、所有提取文件和两包相同 guard。controller ZIP 必须符合既有确定性编码。当前已安装的两份 guard 必须与正式包相同，不借本次修订发布任意新 Core 控制策略。

仅写五个现存文件：

1. `operations/r25-publisher/vmm_controller.py`
2. `operations/r25-publisher/vmm_lifecycle.py`
3. `operations/r25-publisher/vmm_service.py`
4. `runtime/config/vmm-role.json`：仅 `service_script_sha256` 值随 service 模块更新；role 仍 enabled=false。
5. `runtime/handoff-control.env`：仅四个 VMM controller/lifecycle/service/role 摘要值更新，其他原字节保持。

另外保存新的不可变 `MODULE_REVISION_ACTIVE.json` 和 `module-revisions/<approval-sha>` 证明。旧角色批准不会被改签为新 service；运行 guard 先校验新修订，再校验原 overlay、role、Core successor 链，得到有效后像。原始 lifecycle adapter、Core/AI/Web、数据库、配置策略和 generation 行为不变。

安装时 VMM 必须 disabled、desired STOPPED、process=null、gate=CLOSED；拒绝端口5001任何监听、DISPATCHED 或未确认 UNKNOWN。原 lifecycle/gate 完整字节也进入保全快照。模块安装和恢复从不写这两份账本。

## 正式打包与非活动暂存

遵循 feature→main 审查、同仓库 main→release 合并提交。`Package reviewed R25 controller` 仅打包；两个 artifact 必须来自同一次正式 release 工作流。先独立验证 GitHub artifact ZIP digest，再验证 inner archive/manifest/全文件摘要，两层不能混称。

固定私有暂存：

```text
$R/operations/r25-controller-candidates/$DEPLOY_RELEASE_SHA/
$R/operations/r25-vmm-candidates/$DEPLOY_RELEASE_SHA/
```

目录0700、文件0600，沿用 root:root 合同。未有正式制品时不能编造最终 hash、安装批准摘要或把测试 fixture 的身份用于生产。

## 生成实际 unsigned plan

由执行者根据核实的制品生成私有 `runtime/vmm-controller/MODULE_REQUEST.json`，而非要求用户手算 hash。精确字段：

- `schema=R25_VMM_MODULE_REVISION_REQUEST_V1`
- `deploy`：VMM extension 的 `repository,ref,commit,run_id,run_attempt,archive_sha256,manifest_sha256,tool_sha256`，tool 指 vmm_controller.py。
- `controller_deploy`：相同 run/attempt 的 controller identity，同样字段，tool 指 upgrade_assistant_controller.py。
- `review_evidence_sha256`：真实审查记录 `MODULE_REVIEW.json` 的摘要。不能生成虚假 CODEOWNER 或用户批准。

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3
: "${DEPLOY_RELEASE_SHA:?verified formal release required}"
BUNDLE="$R/operations/r25-vmm-candidates/$DEPLOY_RELEASE_SHA"
: "${REQUEST_SHA256:?actual MODULE_REQUEST.json digest}"
"$PY" -B "$BUNDLE/vmm_controller.py" prepare-modules --request-sha256 "$REQUEST_SHA256"
```

prepare 取得原共享 lease，只生成 `module-plans/<request>/UNSIGNED_PLAN.json` 和 before/after sidecars；不安装。其真实 digest 才是后续用户批准对象。旧 watchdog 若持锁，先按既有有界协调程序用原监督入口暂停，完成准备后恢复并独立观察；不能删除/替换锁，也不能停监督等审批。Core/AI/Next 保持运行。

## 具体批准后的安装

将获批计划原字节保存到 `MODULE_APPROVAL.json`；批准范围仅五文件修订、技术证据和短暂 watchdog 协调，不能包含初始化、生成或 OPEN。

```bash
: "${APPROVAL_SHA256:?separately approved exact plan digest}"
"$PY" -B "$BUNDLE/vmm_controller.py" preflight-modules --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/vmm_controller.py" bind-modules --approval-sha256 "$APPROVAL_SHA256"
```

preflight 在原 lease 内验证所有前像。bind 调用原 supervisor stop；再次取得原 lease，确认 watchdog 已停、应用事务终结及完整前像，再 snapshot/CAS/fsync 写入。每个模块、role 和 handoff 均保留原 owner/mode。新 marker 后进入 VERIFY_INTENT，实际运行证明链通过才记 INSTALLED；退出 lease 后原入口恢复监督。不能以手动复制替代该入口。

如果 watchdog 的长持 lease 使 preflight 返回 busy，先完成已有协调窗口，再执行；不要绕过 preflight 或增加重试。模块安装不要求 Java/AI/Next 停机，不承诺生产耗时。运行时真实身份、认证只读 GET 和分离连接的 watchdog 存活仍需安装后实际验证。

## 恢复和消费安全

```bash
"$PY" -B "$BUNDLE/vmm_controller.py" recover-modules --approval-sha256 "$APPROVAL_SHA256"
```

- NOT_STARTED：只报告，没有安装或监督动作。
- INSTALL_INTENT / VERIFY_INTENT / RECOVERY_REQUIRED / RESTORE_INTENT：保留同一事务，要求 watchdog 已停、role关闭、账本及保全快照未漂移；仅按 before/after CAS 恢复五文件，移除只属于该事务的新 revision marker。恢复可中断再进入，不能覆盖外来改动。
- INSTALLED：核验后像和完整证明，恢复监督；**不回滚 consumer**，也不恢复历史 lifecycle/gate 快照。后来新增的 command/window 消费、UNKNOWN 和请求状态保持。
- RESTORED：核验前像并恢复监督。不会再次执行该计划的安装；新安装须重新准备及审核。

若部分状态恢复前发现账本变化、未知进程、外来文件或证据不符，拒绝而不是“恢复旧快照”。已提交后没有降级入口，避免旧 consumer 面对新消费状态。恢复不会退款、重开旧许可或重新发起 generation。共享锁文件永不删除/重建。

## 更新验收工具与下一窗口

V2 协议见 [managed lifecycle](vmm-managed-lifecycle.md)。新 service 会改变 role digest；生产者、验收器（包括其重新导入的 builder）和 observer 必须绑定**实际安装后的新 role/模块 hash**。仅在内存改一个常量不够，也不能复用旧 startup 计划。现场工具不含在固定25+4安装包内；其原文件必须保留，新副本完整hash列入下一 startup 计划并审查。

未来步骤依次为：本次修订正式安装及只读验收→准备全新 CLOSED startup→用户批准一次初始化/900秒→实际 FIRST_READY 起600秒内终端另批单次音乐→至多一次 workflow、creation 和3秒音乐，UNKNOWN 消费且无重试。新 startup 之前确认已安装 consumer 明确支持 V2；旧批准和已消费命令仍拒绝。本 PR 本身不授权任何启动或音乐请求。

## 离线证据边界

测试使用真实临时文件、manifest、flock、CAS/fsync、实际 prepare/bind/运行 overlay/recover；覆盖精确身份拒绝、每个写入点和 marker 后故障、恢复再次中断、账本漂移拒绝、已提交后 UNKNOWN/新消费保留。监督与应用进程身份是明确 synthetic OS 边界。V2 CPU 子进程/HTTP/期限/消费测试保持；实际 producer/receiver/executor→安装后 consumer 的合成链由本地证据绑定。

离线通过不证明 GPU readiness、生产安装成功或真实音频生成。没有修改模型参数、token、生成超时或重试策略。
