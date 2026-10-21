# 已完成 Assistant successor 后的 Core-only 发布

适用于当前已安装 Assistant V2，且原 successor 的 Core、AI/G1 均真实 `COMMITTED` 后，仅发布新的兼容 Core 功能。Gallery daily/关键词修复是功能变更，不是 V1 等价重打包。该模式不初装、不重放 successor、不发布 AI。它不是任意后续业务升级器；前置 policy 必须含原有限 `successor`，不能借此覆盖任意 policy 或再次绑定已有 `core_followup`。

## 必须保持的身份与权限

新 schema `R25_ASSISTANT_CORE_FOLLOWUP_BINDING_V1` 使用原 private owner/delegated review 机制、R25/c5ea shared lease、`CONTROL_UPGRADE_ACTIVE.json` 和 `controller-upgrades/<approval>/journal.json`。没有新批准平台或第二人规则。技术准备产生 unsigned plan，不替代实际 owner 授权或 CODEOWNER/GitHub environment approval。

- 正式 Deploy main→release package-only archive/manifest、repository/ref/commit/run/attempt/tool SHA 全部核验；canonical archive 仍固定25文件。原 legacy/successor 文件集合不变；本模式固定17 publisher文件（原16加已存在的 `core_acceptance.py`）和7 operational targets。仅5个 publisher 模块可变化：`core_business.py`、`core_release_guard.py`、`upgrade_assistant_controller.py`、`core_runtime.py`、`core_acceptance.py`。没有候选自选脚本或目标。
- 新 Core 独立验证正式 incoming JAR/report/manifest、真实release SHA/run/attempt、owner/mode、字节数和payload fingerprint；旧 accepted record 保留其原 POLICY digest。新候选记录必须绑定新 POLICY，不能重标旧记录。
- 原 controller approval/journal 必须精确匹配当前已安装 POLICY；ACTIVE只能指向该原安装或本次原计划。当前 Core CURRENT/JAR对、manifest、application、Java/Guide/Critic pid/ticks及c5ea status独立重验。
- 当前 frontend selection/reference 两个固定文件按SHA绑定，防止等待期间已有Web发布；`assistant.artifacts.web`仍是历史兼容桥接证据，不能冒充现场Web身份或未来Web验收。`upgrade.web_commit`只是拟发Web源码。
- 原 AI CURRENT、ACTIVE、COMMITTED journal、snapshot、accepted record及全部27源文件逐一核验；snapshot内原policy、Core和AI正式run/source/archive必须互相一致。原policy另存内容寻址的 `core-followup-baselines/<sha>.json`，原journal/record不改。仅有G1文件hash不够。任意未完成、恢复中、第三方源/配置/receipt漂移拒绝。
- 新 `upgrade.core_followup`取代原policy中的有限`successor`，冻结已接受AI/G1证据。新旧Core启动、CURRENT与应用恢复均使用同一实际G1 application字节，不再错误要求恢复旧G0。旧successor入口及其“AI/G0先于Core/C0回退”规则保持原样。
- PostgreSQL账户/ACL、V001–V016及校验和、poetry资产/no-map、private attachments、quota/cleanup/backup、Guide/Critic配置、AI policy/wrapper/lifecycle、Web/Next owner都不改变。活动backup即使没有持锁，非终态journal也拒绝本次绑定。

## 生成可审查私有计划

这些是未来完成正常PR审核及正式build后的操作命令，不是本次生产执行记录。执行者负责从实际官方manifest、已保存兼容/验收证据和当前私有review记录生成 `runtime/core-publications/CORE_FOLLOWUP_REQUEST.json`，0700目录/0600文件，不要求用户手算hash或填写policy。

Request严格字段：

|字段|实际来源|
|---|---|
|`schema`|固定 `R25_ASSISTANT_CORE_FOLLOWUP_REQUEST_V1`|
|`deploy`|正式Deploy `repository,ref,commit,run_id,run_attempt,archive_sha256,manifest_sha256,tool_sha256`|
|`core`|正式Core `commit,run_id,run_attempt`；原incoming路径必须已有对应真实JAR/report/manifest|
|`web_commit`|已存在且审核过的拟发Web源码SHA（feature/main均可），不是预测的未来merge SHA；真实Web release后核验其包含该源码，不为填写此字段提前合并会部署的release PR|
|`evidence`|原V2严格结构 `review_id,compatibility_sha256,acceptance_sha256`，引用实际证据|
|`compatibility`|最新只读保留清单 `retention_manifest_path,retention_manifest_sha256`，采用既有生成/核验程序|
|`compatibility_evidence_sha256`|旧Core+新schema/已保留数据的真实兼容证据SHA|
|`review_evidence_sha256`|真实 `CONTROL_UPGRADE_REVIEW.json` 摘要；不得生成假人类review|

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3
: "${DEPLOY_RELEASE_SHA:?actual reviewed formal release SHA required}"
BUNDLE="$R/operations/r25-controller-candidates/$DEPLOY_RELEASE_SHA"
REQUEST_SHA256="$(sha256sum "$R/runtime/core-publications/CORE_FOLLOWUP_REQUEST.json" | cut -d ' ' -f1)"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" prepare-core-followup --request-sha256 "$REQUEST_SHA256"
```

准备命令持原共享lease，只读现场和正式incoming，生成本bundle的固定private sidecars和 `core-followup-plans/<request-sha>/UNSIGNED_PLAN.json`。它不写 `CONTROL_UPGRADE_APPROVAL.json`、CURRENT、live policy/application，不停止服务，不做DB写入。输出计划SHA；重复同一未漂移请求只接受逐字相同产物，冲突sidecar拒绝覆盖，保留旧计划。若现场漂移，应保留旧计划及sidecars并经真实重新评审处理，不手改旧digest。

Unsigned plan包含实际所有before/after hash、正式artifact绑定、当前进程/前端/旧controller context。执行者将完整diff及该digest交既有授权review流程；只有真实计划授权到位后，按既有私有方式将批准的完整plan保存为 `CONTROL_UPGRADE_APPROVAL.json`，0600。prepare的 `approved:false` 不能转称approved。

## 审核和执行顺序

1. Core关键词修复PR、Deploy followup Draft PR分别正常审核和必需CI；Core先main→release，Deploy先main→release正式package-only。Core发布开关保持禁用，正式build不得自动publish。PR CI或本地ZIP不是正式release artifact。
2. 正式包和Core制品按原路径受控交付，运行上述prepare，完成实际私有plan review/批准。确认无活动application/backup；原watchdog协调窗口下使用原锁，不删锁、不另起publisher。
3. 使用正式bundle执行下面的preflight/bind；只暂停并恢复watchdog，Java/Guide/Critic/Next身份保持，安装后使用重新加载的新engine验证，独立检查watchdog持续存活。
4. 原Core publisher发布：停止Java/watchdog，做原数据库+private-file一致性backup，V001–V016已应用链/校验和核验，然后启动新Core，执行原Knowledge/Assistant以及新Gallery read-only acceptance，再COMMITTED。没有新migration/down migration。Guide/Critic/Next/Tunnel保持。停机时长要实测，不保证零停机。
5. Core验收完成后才合并/运行Web release，并遵守真实production environment approval与c5ea唯一Next所有权。Web不能先上线daily查询依赖。

```bash
: "${APPROVAL_SHA256:?actual approved exact plan digest required}"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" preflight-core-followup --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" bind-core-followup --approval-sha256 "$APPROVAL_SHA256"
: "${CORE_SHA:?actual formal Core release SHA required}"
: "${CORE_RUN:?actual formal run required}"
: "${CORE_ATTEMPT:?actual formal attempt required}"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" stage --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" publish --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" status
```

新followup固定 `acceptance=gallery-daily-v1`：仅新候选（不对旧Core恢复）做有限、无生成的Gallery GET。检查source/daily总数和分页、Asia/Shanghai有效orderDate、重复相同日期顺序、下一页无重复、从已读公开title取短literal关键词验证两种排序。跨午夜、API拒绝、空或畸形响应明确失败，不把HTTP200等同验收。特殊 `%/_/反斜线` 的完整语义由真实PG回归另证。Web仍需实际公开手机/桌面Gallery验证。

## 按实际事务阶段恢复

- `prepare`/未有controller journal：`recover-core-followup` 返回 `CORE_FOLLOWUP_NOT_STARTED`，不停止服务、不回滚应用。
- 绑定部分复制/响应不确定：停止再bind；读取原approval/journal，运行同一digest的 `recover-core-followup`。已INSTALLED且身份一致返回NOOP。恢复旧精确控制文件/policy/handoff并恢复watchdog，拒绝第三方漂移；不触碰AI CURRENT、旧accepted records或应用数据。
- Core publish未开始：不运行Core recover。实际部分事务使用原 `recover --transaction`；已COMMITTED明确回退使用原 `rollback --transaction`。新followup下回退仅Core到已审baseline，AI/G1保持不变。既有forward-schema recovery只读核验，保留后续写入和删除墓碑，不恢复旧DB或private-file备份，不重派unknown模型任务。
- 若新Web已上线，先通过其c5ea原事务恢复兼容Web，再回退Core；不能先撤掉daily依赖。若需撤销本controller绑定，必须先恢复其精确旧Core/前端context，不能在新Core仍CURRENT时恢复旧policy。
- 真实失败/不确定状态必须恢复原tx，不新建publication替代；PID/配置/保留清单漂移则停止并重新取证，不能放宽检查。

```bash
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" recover-core-followup --approval-sha256 "$APPROVAL_SHA256"
# 仅在实际存在且需要恢复的Core事务上运行：
: "${CORE_TX:?actual original Core transaction required}"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" recover --transaction "$CORE_TX"
# 仅已COMMITTED、已评审兼容回退且Web依赖已处理时：
"$PY" -B "$R/operations/r25-publisher/core_publish.py" rollback --transaction "$CORE_TX"
```

本轮测试是真实临时文件/descriptor/flock/fsync/CAS和实际guard/plan/publisher衔接逻辑；OS服务、正式GitHub审批和完整PG契约在controller单元fixture中模拟，不能冒充生产安装或回退。原非空PG/backup证据仅在未改变范围内复用。新PG关键词测试另在真实隔离PostgreSQL执行。无provider/RAG请求。

## 后续 managed VMM Core 的专用入口

上述 Gallery V1 仍是单次绑定，不可再次调用来冒充新 Core 的批准。Gallery 已 COMMITTED 后，新增有限的 [managed VMM Core followup](managed-vmm-core-followup.md) 支持正式新 JAR 与五个私有门禁配置的成对转换、固定音乐在途聚合和原事务恢复；它保持原 Gallery/AI provenance，并明确 Core 先于 disabled VMM overlay 的安装顺序。
