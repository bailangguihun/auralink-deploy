# 已安装 Assistant V2 的后续业务发布

本次改变是显式作品选择/权威 Core 查询/有据回答和既有 Flash 参数修复，**不是 V1 等价重打包**。沿用 R25/c5ea 锁、可信正式包、私有批准、原事务和恢复入口，不增加启动器或守护器。

## 受审状态与有限变换

状态链固定为 `C0/A0/G0 → C1/A0/G0 → C1/A1/G1 → Web`。C0 是实际已验收 Assistant Core，不是历史 Knowledge JAR。数据库保留 V001–V016；没有新 SQL、down migration 或数据库恢复。Core 原发布事务仍做一致性备份并只读核验完整 schema。

1. 安装 successor controller/policy。实际已安装 Core 为 `R25_CORE_BUSINESS_POLICY_V2`、`assistant-v1`；AI 为 `R25_AI_SOURCE_POLICY_V2`/27 files。已有 wrapper、lifecycle 和 AI POLICY 必须逐字不变。旧控制文件可以来自已记录的不同正式包：每个 before hash 独立精确绑定，不能要求旧 upgrader 被假称已整体更新。
2. 原 `core_publish.py` 发布 C1，配置 G0 的 application.yml **字节不变**。新的 Core policy 精确绑定新正式 JAR、C0 accepted record、当前兼容性/保留清单及正式 AI 候选身份。
3. 原 `ai_publish.py` 的同一个 AI transaction 发布 A1，写入 AI JSON G1 和 Core application 中唯一 `execution.expected-config-sha256` 行。只允许 Beijing DashScope `qwen3.8-flash` 从 absent/provider-default 变为 `thinkingMode:"disabled"`，使已验证适配器输出 boolean false。其他模型、limits、密钥路径、RAG disabled、Guide/Critic 配置、存储政策必须完全相同。只允许变化 role=`critic`。
4. 原 c5ea Web 工作流最后进行。`upgrade.web_commit` 是待发 Web source；Assistant compatibility artifacts.web 继续绑定已经实际验收的桥接 Web artifact，不能用尚未存在的 future build hash 填空。这项旧 Web 兼容绑定不构成新 Web 验收。

G1 不是“磁盘有这个 hash 就放行”。Core guard 读取固定 AI CURRENT/ACTIVE/journal、snapshot hash、真实 accepted record，并校验 AI 正式 repository/commit/run/attempt/source-tree/archive 和 C1 CURRENT。PREPARED/RESTORED 只准 G0；START_INTENT/COMMITTED 只准 G1（COMMITTED 必须匹配 AI CURRENT）；混合写入阶段只允许受锁恢复观察，禁止应用启动。Core POLICY/CURRENT/accepted record 在 AI 配置转换中不重写，避免同 JAR 不同配置产生循环引用。

## 正式包、私有输入与既有批准机制

新源码先走 Deploy 正常 feature→main→release 审查/正式 package-only workflow。本地 ZIP、mock run 或单纯 hash 不是正式批准。运行工具必须位于实际正式 commit 的 `operations/r25-controller-candidates/<commit>/`；完整 canonical 25-file archive 和 manifest、tool hash、owner/mode 都校验，拒绝字节码缓存。

私有输入仍使用既有 owner/授权评审流程。没有新增第二人要求或新平台；程序不能证明聊天中的人读过新 diff，更不能生成其签名。`CONTROL_UPGRADE_REVIEW.json` 延用现有 launcher repair 的私有 review-evidence 格式（JSON 对象，精确 digest）；计划通过 `review_evidence_sha256` 引用它。`CONTROL_UPGRADE_APPROVAL.json` 是批准后完整计划，schema=`R25_ASSISTANT_CONTROLLER_SUCCESSOR_V1`。未签/缺字段/占位符文件不能执行。

计划字段：

- 原有 `from_policy_schema,deploy,current_release,next_owner,controller_owner,private_owner,files,operational,review_evidence_sha256`；from 是真实 V2，next_owner=`c5ea`。
- `deploy`: repository=`bailangguihun/auralink-deploy`, ref=`refs/heads/release`, commit、run_id、run_attempt、archive_sha256、manifest_sha256、tool_sha256 均来自正式执行。
- `files/operational`: 原 installer 固定集合，所有 before 必须存在；每项 before/after SHA。wrapper/lifecycle/AI policy 前后相同。没有任意目标路径。
- `current_ai_release`: 实际 commit/source_tree/record；`preserved`: release-jar/preview-jar/manifest/application 的实际 hash；`services`: java/guide/critic 的实际 pid/ticks。安装前在同一个 shared lease 内重验；进程或文件漂移需要重新审查计划，不能改旧摘要。
- `POLICY.next.json`: 新 Core policy，新增 `upgrade.successor`；`AI_POLICY.next.json` 与现有 AI policy 完全相同；`HANDOFF.next.env` 仅更新固定 controller/policy 摘要；`ASSISTANT_CONFIG.next.json` 是严格验证后的 G1。
- `upgrade.successor`: `schema=R25_ASSISTANT_SUCCESSOR_CONFIGURATION_V1`, `ai_before`, `ai_candidate`（repository/commit_sha/github_run_id/github_run_attempt/source_tree_sha256/archive_sha256）, `ai_configuration`（before/after hashes）, `core_final_sha256`。

G1 同时在 AI 私有 store 的 `successor-configurations/<G1sha>.json` 受控交付，0600/既有 private owner；不含新 secret 值，不从任意路径读取。入站 Core/AI 制品继续由各自原 stage 命令验证真实 run/attempt、blob、manifest 和依赖清单，不能由这个安装工具代替正式 provenance。

## 精确入口与前置边界

下列命令只用于完成正常审核、正式包和真实计划绑定后的获准执行窗口；本文不是运行记录。`BUNDLE`/`APPROVAL_SHA256` 必须实际赋值，不能填合成 run。

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3
: "${DEPLOY_RELEASE_SHA:?actual formal Deploy release commit required}"
: "${APPROVAL_SHA256:?approved private successor plan digest required}"
BUNDLE="$R/operations/r25-controller-candidates/$DEPLOY_RELEASE_SHA"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" preflight-successor --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" upgrade-successor --approval-sha256 "$APPROVAL_SHA256"
# 仅安装 journal 表明部分失败时，恢复原计划；INSTALLED 返回核验 NOOP。
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" recover-successor --approval-sha256 "$APPROVAL_SHA256"
```

安装仅暂停/恢复 watchdog，保持 Java/Guide/Critic/Next 实际身份。复制完成后使用新建 lifecycle engine 再核验当前应用，不能让 watchdog 继承旧 guard 模块缓存。部分安装恢复使用保存的生命周期字节，先读取既有 Critic execution policy 供身份校验，不在混合文件状态 full-load 或重启应用。

然后使用现有 Core/AI CLI（真实 SHA/run/attempt 必须已绑定 policy），不增加第二发布入口：

```bash
"$PY" -B "$R/operations/r25-publisher/core_publish.py" stage --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" publish --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
# Core bridge 验收成功后才执行 AI，已审 G1 在内容寻址位置且权限正确。
"$PY" -B "$R/operations/r25-publisher/ai_publish.py" stage --commit "$AI_SHA" --run-id "$AI_RUN" --run-attempt "$AI_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/ai_publish.py" publish --commit "$AI_SHA" --run-id "$AI_RUN" --run-attempt "$AI_ATTEMPT"
```

Core 发布会暂停 Java/watchdog，完成原 DB/private-file 一致备份后启动 C1。AI 发布会暂停 Java/watchdog、重启变更的 Critic，加载 G1，再启动同一 C1 并做已有认证只读 Assistant acceptance。Guide/Next/Tunnel 不重启。停机时长要实际测量，不能承诺零停机。

## 失败与回退

- controller PREPARED/部分 copy：原 control journal + before 文件，`recover-successor`，不重新初装。
- Core NOT_STARTED 不 recover；实际部分 Core tx 使用 `core_publish.py recover --transaction "$CORE_TX"`。完整已提交使用原 rollback。
- AI 未开始不 recover。部分源码/两配置失败记录原 tx `RECOVERY_REQUIRED`，原 `ai_publish.py recover --transaction "$AI_TX"` 恢复 A0/G0，保留 C1。已 COMMITTED 的显式回退使用 `ai_publish.py rollback --transaction "$AI_TX"`。
- 先恢复 AI/G0，再考虑 Core/C0。Core rollback 在任何 stop/mutation 前明确拒绝 AI 仍为 G1；不删除 snapshot、不改 CURRENT、不盲重试。
- AI snapshot 保存两配置原字节及 after 字节、policy hash；源/配置有第三方漂移立即拒绝覆盖。可能已写 CURRENT 但响应丢失时读取实际 journal/pointer，恢复同一个 tx，绝不新建 publication 作为 workaround。
- 不恢复旧数据库、不删除 V014–V016、不撤销新会话/附件/删除墓碑、不重新分发 unknown model run。

## 验证边界

`test_assistant_successor.py` 覆盖有限配置、正式身份/漂移拒绝、两配置成功/失败/uncertain commit 恢复、真实本地文件安装/CAS/flock/fsync。OS 服务、网络 health、正式项目审批和完整 PG policy fixture 明确模拟；既有非空 PostgreSQL rehearsal 复用，不冒充本轮 production acceptance。独立保存的实际 lifecycle 源边界证明用于补充 execution-policy 初始化及旧 guard cache 风险。本轮不执行真实模型、RAG 或生产操作。
