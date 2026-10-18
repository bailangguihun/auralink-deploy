# Assistant 候选发布及恢复契约（S4，本地实现，未批准生产）

本合同扩展原 `Publisher/CoreRuntime`、共享 `frontend-deployment.lock`、Core/AI CURRENT/ACTIVE 和 c5ea 所有权。没有 Assistant 独立生产启动器。S4 的未提交源码、合成 review/run/commit 或本地预览构建不能作为正式 release/私有批准。

## 不变边界与候选身份

- 原 Core V1 等价重打包仍要求完全相同 payload；原 Knowledge V2 仍只接受 V001–V013。只有安装了明确 `upgrade.capability=assistant-v1` 的 V2 后继合同才允许精确的 Assistant 链。
- `assistant_release.MIGRATION_SHA256` 固定 V001–V016 每个 SQL 文件的名字/字节摘要；调用方提供另一 SQL 并重算 manifest 摘要仍被拒绝。允许前像仅完整 V001–V013 或 V001–V015；最终必须完整 V001–V016。V014/V015 不被重写。SQL 与账本同一事务提交，已提交完整链是只读 NOOP。
- 安装合同绑定 Core、AI、Web、Deploy 的独立仓库/完整 source commit/制品摘要、前版已验收 JAR、旧 V2 POLICY/payload、兼容性证据、应用配置、存储政策、评审证据和当前服务身份。底层 isolated helper 可读 `ISOLATED_TEST_ONLY` 测试政策，但实际 Core POLICY 验证与 runtime hook 都拒绝该状态，必须是已审 `PRODUCTION_REVIEWED`，且这一字符串本身不是批准证明。只格式正确不构成批准；入站 Core/AI 正式候选还必须通过原有独立 run/attempt/blob 校验及 installed POLICY 精确比对。
- AI 保留 V1 的 19 文件/历史仓库识别；V2 明确为转移后的 `bailangguihun/auralink-ai-service` 和完整 27 文件。拒绝未知模块、依赖变更、漏包、自批准和旧仓库重贴标签。新增 8 文件在 journal 中是 before=absent；只有本次创建、after-hash 匹配的源码可移除，目录必须为空。不会 glob 删除未知文件。

## 配置与启动模式

Core 的精确配置集合由 `core_business.render_configuration` 控制，保留 Knowledge 两个诗词文件、sealed-memfd-capdrop 和 map=false：

| 模式 | enabled | history-only | execution.enabled |
|---|---|---|---|
| disabled | false | false | false |
| history-only | true | true | false |
| enabled | true | false | true |

执行配置仅新增 `execution.ai-base-url`、`execution.allow-loopback-http`、`execution.internal-token-file`、`execution.expected-config-sha256`；存储仅新增 `attachment-root`、`storage.quota-bytes`、`storage.draft-lifetime`、`storage.minimum-free-bytes`、`storage.cleanup-enabled=false`。政策 hash 是 storage.* 子集排序紧凑 JSON SHA-256，并与最终 application.yml hash 同时绑定。运行时环境/Java 参数不得覆盖 Assistant 配置。

`assistant_runtime_overlay` 只从已审私有 provider env 转发 `AURALINK_ASSISTANT_CONFIG_FILE`、`AURALINK_ASSISTANT_INTERNAL_TOKEN_FILE` 两个引用，不改 PORT、QWEN、LLM、Guide、Critic 模型或既有令牌。缺失/格式坏的 Assistant 引用不使 Critic wrapper 停止；AI 自身把 Assistant 标为 DISABLED/MISCONFIGURED。生产启用前须在私有渠道审查并绑定该 provider env 的精确前后摘要、两引用目标、读取身份、AI JSON hash 和文件权限；不能把本地 fixture 值复制到生产。配置切换需要原 lifecycle 重启对应 Critic 进程，不是热更新。

2026-10-14用户已批准的首发政策见本文末节；历史S4本地测试数值不构成生产政策。生产 cleanup 仍 false；启用 cleanup 需要单独审查政策和 Core 维护命令的私有输入。没有短期自动删除 request/session 墓碑。

## 安装与升级

首发明确使用 `upgrade.assistant.retrieval_profile=artwork-only-v1`，外部 RAGFlow 延后。AI JSON 必须包含 `ragflow.enabled=false`，不交付 RAGFlow origin、凭据或 dataset，两个 DashScope 模型及整个 AI JSON 字节仍通过 Core `execution.expected-config-sha256` 绑定。持续生产模型费用采用用户已批准的人工监测与手动暂停，详见末节；不复用S5预算。

该 profile 的八次只读验收额外要求 Core 模型 registry 的 `retrieval` 精确为 `{"state":"NOT_CONFIGURED"}`；这是当前 AI 对未启用外部检索的现有输出，并非软件版本或请求成功证明。registry 返回 AVAILABLE、遗漏字段、额外外部地址或未知 profile 一律拒绝。每个新 run 的 `retrieval.ragflow=false` 和 `DISABLED` 状态、无 RAG 请求及无文档引用，需要独立应用链证据；不从 registry GET 伪造这些执行结果。Core 作品库检索继续按其自己的权限/UUID/当前 revision 投影，不被外部检索开关关闭。既有 S4/历史未带 profile 的记录保留原验收合同，不能将其冒充首发 `artwork-only-v1` 候选。

1. 后续获准现场只读预检：查实际 CURRENT、controller schema、AI hook 安装状态、source owner/group、服务 PID/ticks、共享锁、PostgreSQL credential account、pgpass 权限、父目录可穿越 ACL。这里不猜测 root:root 或历史 UID。
2. 已安装 Knowledge V2 使用 `upgrade_assistant_controller.py`，不能重新执行 V1 installer 或把 V2 伪装 V1。若 AI controller 尚未安装，先走原 `install_ai_controller` 的受审初装；此后才可做显式 AI V1→V2 policy 更新。
3. 私有 `CONTROL_UPGRADE_APPROVAL.json` 使用 `R25_ASSISTANT_CONTROLLER_UPGRADE_V1`，固定模块集合、操作文件集合、正式 Deploy identity、CURRENT、before/after hash、实际 owner 和真实 review evidence。角色/批准沿用既有 owner/授权评审流程，本文件不新增第二个人或伪造批准。
4. 控制文件候选在 `operations/r25-controller-candidates/<formal-deploy-commit>/`；真实私有 POLICY.next/HANDOFF.next/AI_POLICY.next 不进 Git/正式源码 ZIP。包装器从保留的完整源字节渲染，lifecycle 仅改相应包装器精确摘要，handoff 仅更新列明的绑定。
5. 命令（尚未执行生产）：

```bash
python3 -B /reviewed/fixed/bundle/upgrade_assistant_controller.py upgrade --approval-sha256 "$REVIEWED_APPROVAL_SHA256"
# 若 journal 已 INSTALLED，仅验证 NOOP；部分执行必须 recover，不能重试覆盖
python3 -B /reviewed/fixed/bundle/upgrade_assistant_controller.py recover --approval-sha256 "$REVIEWED_APPROVAL_SHA256"
```

`/reviewed/fixed/bundle` 不是生产默认值，必须替换成上述正式 commit 目录；CLI 自身强制实际位置一致。升级仅暂停/恢复原 watchdog，不停止健康 Java/Guide/Critic/Next；PID/ticks 和 c5ea status 前后相同。源文件/策略的未知漂移拒绝覆盖。部分升级使用绑定的 lifecycle/handoff 前像恢复，不用混合控制器重新解释政策。

## 应用顺序与停机

受审 controller → AI 来源及 Assistant 私有配置（先禁用）→ Core 受审 schema/application → c5ea Web。AI 源码发布复用原 AIRuntime：暂停原 watchdog/Java，只停止变更的 AI role；Guide 未变不重启。Core 发布停止原 watchdog/Java后，`Publisher` 才调用 `prepare_forward_schema`；备份和 SQL 均受相同 lease，并再次证明无 executing run/上传写入。Next 只能由 c5ea 发布。预计相关 Java/Critic 有停机；S4 没有测生产停机时长。

新建附件根是独立受审配置交付步骤：专用 owner、0700、精确 `.assistant-attachments-v1` 标记和服务账号权限；不可复用 Gallery/诗词目录。V001–V013 首次发布前也须完成这个空命名空间的交付；helper 不会在未知目录初始化或递归改权限。已有 V015 数据必须复用自己的真实附件根。

## 备份、迁移、重入和恢复

- `assistant_release.PostgresControl` 执行真实 psql/pg_dump/pg_restore；二进制摘要与被审连接配置绑定。credential UID/GID/groups 查 OS 实际记录，必要时子进程使用现有身份运行，pgpass 不改 owner/ACL。dump 文件由 controller 预先打开 FD，数据库账号无需扩大备份目录权限。
- 同一静止窗口内：全数据库一致性 pg_dump + 私有附件全部精确文件 + ledger + 前后逻辑数据库 hash/文件 hash 比较。PG 安全补丁的随机 `\restrict` token 从逻辑比较中排除，数据不排除。私有备份不可进入分发制品。
- `MIGRATION_INTENT` 在发送 SQL 前落盘；整个差集 SQL 与账本一个事务。超时/断开为 `MIGRATION_RESULT_UNKNOWN`，只检查实际链，不自动重发。部分链、checksum 冲突、其他版本均拒绝；只有完整已知前像可后续审查再次执行。
- 安装/启动失败沿原 Publisher 恢复已审兼容应用，不回退 schema、不删除 Assistant 表、不还原旧整库、不重发 uncertain provider run。迁移已提交时保留 V016 和后续新增行/删除墓碑。
- `restore_disposable` 只接受明确 disposable 的空 DB、全新附件目标；验证 manifest/dump/hash、真实 pg_restore、最终文件 bytes/hash 和 ledger。它验证备份时点，而不拥有备份后删除事实。**旧备份不能直接成为服务恢复源；上线旧备份需要当前删除/撤权日志的额外一致性审查。例行应用回退绝不调用它。** 无破坏式 down migration。
- NOT_STARTED 不调用 recovery；PREPARED 仅证实原件后终止；混合控制/应用文件要求各自 journal 恢复；COMMITTED 使用原显式 application rollback 且前版必须仍兼容；未知/外来漂移保留证据并停止写入。

## 保留的S4证据边界与后续现场绑定

实际 S4 报告在迁移 workspace 的 `stages/assistant-storage-release-s4-20261014/`，不是仓库 CI/生产批准。真实 PostgreSQL 的非空 V13→V16、V15→V16、故障事务/丢回复、pg_dump/恢复、墓碑/图像字节，以及实际 Publisher+CoreRuntime 文件事务在同一 rehearsal 中执行。OS lifecycle/正式 GitHub批准/生产 provider/公网不属于该 fixture 证明。

本地 controller 事务测试确实使用真实 flock、目录、hash/CAS、fsync、before/after 文件和部分失败恢复；服务健康、PID 和 business-policy 夹具明确标记。AI 新文件切换是实际 AIRuntime；provider/OS进程为夹具。不能称生产回滚验收。

S4记录中的现场事项已由后续只读记录补充：2026-10-14观察到AI controller尚未初装，生产Core已是Knowledge V2，PG service为artlivepg25/999:999、现有pgpass0600；真实AI源码19文件与baseline匹配且owner1000:1000。不得把这些时点观察当永远有效。生产执行前仍需核准私有provider env增量/附件根、正式source/run/artifact及真实review。用户首发政策与S5模型结果见下节及发布交接；S5不等于生产验收，RAGFlow由用户明确延后。此源码轮仅只读现场及隔离测试，未执行生产安装/备份/调度。

## 用户已批准的首发运行政策（2026-10-14）

附件512MiB/账号，READY未发送草稿7天，空闲磁盘下限5GiB；自动附件清理关闭。备份保留7个每日成功集和4个每周成功集。真实模型对既有已认证用户开放，费用由运营人工监测、必要时人工暂停；没有新增金额硬上限或参与者allowlist。RAGFlow关闭。用户政策授权不代替源码CODEOWNER审核、正式release身份或具体生产执行批准。

### 定期一致备份：现有Core CLI，不增加服务守护器

`upgrade.assistant.backup_policy` 绑定以下完整值：

```json
{"schema":"R25_ASSISTANT_BACKUP_POLICY_V1","job_id":"assistant-daily-v1","daily":7,"weekly":4,"utc_hour":19,"utc_minute":15,"weekly_day":5}
```

固定私有位置是 `$R25/runtime/assistant-backups/assistant-daily-v1`，目录0700、记录/数据库包/私图副本0600，由现有controller身份管理；PG仍使用既有pgpass所属账号。`JOB.json`、`ACTIVE.json`、`runs/YYYY-MM-DD.json`、`sets/YYYY-MM-DD/{database.dump,files,manifest.json}` 绑定本job身份与每个完整备份。重复同UTC日的成功执行只核验、不重复停机/备份；失败同日不会自动重新dump。

现有服务器未安装cron/crond/crontab且PID1不是systemd，因此不伪称已安装定时任务。采用现有GitHub Actions作为定时触发器，`.github/workflows/assistant-backup.yml` 只调用已安装、摘要匹配的Core CLI；并非第二个R25生命周期owner。目标时间UTC19:15（北京时间次日03:15），每周标记UTC周六（北京时间周日）。GitHub调度可能延迟/丢失，不保证准点；运营每天核验成功journal，缺失日由相同工作流手动补触发一次，失败不得自动重试。

工作流默认关闭：仓库变量 `R25_ASSISTANT_BACKUP_ENABLED` 不等于`true`则跳过。需要后续批准执行时配置本仓库私有 `SERVER_HOST`、`SERVER_PORT`、`SERVER_SSH_KEY`、`SERVER_KNOWN_HOSTS` 及变量 `R25_ASSISTANT_BACKUP_CONTROLLER_SHA256`（实际已安装core_publish.py摘要）。不复制其他仓库secret，不自动接受SSH主机key，不在PR/feature push运行。未来正式安装并验收后才开启；本轮未配置secrets、未启用变量、未dispatch。主分支workflow变更仍受本仓库真实CODEOWNER/check规则。

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3.10
# 以下仅是未来已授权执行窗口中的现有controller命令，本轮未执行：
"$PY" -B "$R/operations/r25-publisher/core_publish.py" assistant-backup-status
"$PY" -B "$R/operations/r25-publisher/core_publish.py" assistant-backup-run
"$PY" -B "$R/operations/r25-publisher/core_publish.py" assistant-backup-retention
"$PY" -B "$R/operations/r25-publisher/core_publish.py" assistant-backup-retention --apply
```

run持R25/c5ea共享锁，拒绝非终止发布/controller事务和活动模型/上传/Creation/Critic工作，确认V016与当前被审artifact后，只暂停watchdog和Java，使用既有pg_dump+私图复制+前后逻辑/文件hash验证，然后恢复同一Core制品及watchdog。Guide/Critic/Next不重启；Next只能由c5ea管理。这意味着每日一次Java/API服务短暂中断，时长取决于真实数据量，尚未测生产时长；不承诺零停机。启动/公共Gallery与匿名鉴权边界检查复用现有验证，无需长期保存一枚登录token，也不会产生模型请求。不是对新应用候选的替代验收。

失败会尽力恢复同一Core，journal明确FAILED_RESUMED；启动失败或进程被杀为RECOVERY_REQUIRED。新Core/AI发布会拒绝未恢复的备份事务。人工读取真实journal后可用 `assistant-backup-recover`，它只恢复原已绑定Core/watchdog，绝不重跑dump、SQL或模型。无journal时拒绝recover。CURRENT/POLICY漂移一律停下；不切换历史JAR、不restore数据库。

retention默认只读列出保留/删除日期，apply仅删除本job具有完整成功manifest或本次DELETE_INTENT、逐文件hash/类型/权限符合的旧备份；保留最新7每日集与最新4每周集的并集，记录可保留更久。外来文件/软链/未知漂移拒绝，部分删除按intent逐文件核验后续做；没有递归`rm -rf`。publication事务备份、历史手工备份、catalog媒体、用户原图和聊天内容均不在删除范围。失败/不完整备份保留人工检查，不自动当成成功集淘汰。备份恢复仍仅演练到明确空白disposable目标；旧备份不能覆盖后来删除/撤权事实。

### 人工费用监控与暂停

费用监控使用现有Assistant run/usage持久记录和DashScope账单；missing usage/取消后的上游继续执行仍可能产生费用。S5账本不是持续额度账本。手动暂停只禁止新chat/summary admission、claim/pre-dispatch，已越过分发边界的执行可能完成和计费，不自动取消/重试。

```bash
"$PY" -B "$R/operations/r25-publisher/core_publish.py" assistant-generation status
"$PY" -B "$R/operations/r25-publisher/core_publish.py" assistant-generation suspend
# 运营完成费用检查、明确恢复时：
"$PY" -B "$R/operations/r25-publisher/core_publish.py" assistant-generation resume
```

现有已绑定attachment-root中唯一控制文件 `.generation-suspended-v1`，精确内容 `ArtLIVE Assistant generation suspended v1\n`、0600、根目录所属身份。操作持共享锁、校验namespace/非终止事务、以原子写/CAS删除并保留私有INTENT/COMMITTED及前记录；不修改.env、不重启Java/Critic、不清空排队记录。错误文件/权限/软链拒绝；Core对坏控制状态fail-closed。状态、历史、图片、导出、删除仍可用。标记属于受控备份inventory，恢复不暗中删除它或恢复付费分发。

## 已安装 Assistant V2 的后续业务升级

后续 C0/A0/G0 → C1/A0/G0 → C1/A1/G1 不重跑上述首次安装，见 [后续发布合同](assistant-successor-release.md)。该显式合同保留既有 27 文件/16 版本链、共享锁、权限与私有批准；只允许受正式 artifact 与原事务绑定的 Flash 配置相位变换。
