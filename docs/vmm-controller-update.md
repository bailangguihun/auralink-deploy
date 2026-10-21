# 已安装 Assistant V2 的可选 VMM 增量控制器

这是独立、固定范围的控制器扩展，不是第一次安装、Assistant successor重放或等价Core重打包。部署前仍须正常 feature→main→release 审查、正式package-only产物、实际私有计划批准和原R25监督/共享锁窗口。本文命令是后续授权执行入口，本次PR不启动服务或加载模型。

## 不变项与兼容机制

现行Gallery Core-only V2安装的lifecycle为 `783c10bca59e8723c2a208970efd566890776aff8813a536bbc4c8e069d1fda3`，guard为 `8ae03f1879077d526bef83f601df4b59195c139cf654964229d11b8012f69dd1`。`vmm_lifecycle.extend_lifecycle`只接受这份完整原字节并做精确插入，不能用旧fixture整体替换现场。

新增模块：`vmm_controller.py`、`vmm_lifecycle.py`、`vmm_service.py`。还包含最小 `core_release_guard.py` hook，并严格绑定operations与publisher两份相同guard。安装只修改固定8目标：3个新增模块、2份guard、lifecycle、handoff以及初始 `vmm-role.json`。角色初始仅 `{schema:R25_VMM_ROLE_V1, enabled:false}`，没有token、权重或服务目录也不触发启用。

原磁盘Core POLICY、CURRENT、accepted records、原CONTROL_UPGRADE_ACTIVE和批准日志均不改。`core_release_guard.policy()`完成原policy/config验证后，按handoff绑定的VMM controller源码验证正式扩展包、完整批准计划、原V2上下文和INSTALLED日志，**只在内存替换既有controllers的lifecycle/guard摘要及runtime_modules内已存在的guard摘要**。不增加任意可执行模块、不放宽旧policy的schema/应用资格检查、不重标历史记录。CoreRuntime原控制器校验和backup启动路径因此看到受审新摘要。

正常运行的overlay不冻结后续合法Web选择、handoff中的非控制字段或Java PID，也不把安装时的“所有事务必须终结”要求施加到正常Core启动/备份恢复。原Core/AI/config合同和运行中事务仍由原guard验证。VMM与lifecycle/guard的受绑定handoff字段、实际service文件摘要、正式包及原批准policy仍须精确匹配。安装和未完成安装恢复则要求计划中的完整保全快照不漂移。若未来需要改Core POLICY，必须经过新的正常Core发布绑定并精确兼容该overlay；当前旧Core-only followup本身只允许一次后继，不能拿本模块当任意Core发布器。所需Core dispatch gate发布的正式绑定，必须在具体发布准备中单独解决并审核；不得删检查或修改旧accepted record“配合”新JAR。

## 正式扩展包

原固定25文件controller ZIP保持原合同。正常package workflow额外产生 `vmm-extension.zip` 与其独立manifest；实际release commit/run/attempt/archive/manifest/tool摘要均取正式执行，不把PR CI/localZIP当正式release。

ZIP只含排序固定四文件：

```text
core_release_guard.py
vmm_controller.py
vmm_lifecycle.py
vmm_service.py
```

manifest schema=`R25_VMM_CONTROLLER_BUNDLE_V1`，固定字段 `repository,ref,commit,run_id,run_attempt,archive_sha256,files`；repository=`bailangguihun/auralink-deploy`，ref=`refs/heads/release`。files逐项固定`path,bytes,sha256`。拒绝额外文件、symlink、字节码缓存、清单或源码漂移。正式提取位置固定：

```text
$R/operations/r25-vmm-candidates/$DEPLOY_RELEASE_SHA/
```

文件0600/root:root，目录0700/root:root。生产CLI不接受fixture root、替代port、任意脚本或可执行测试backend。隔离tests使用显式Python Runtime API，不能通过环境/CLI选择。

## 准备、批准、绑定

准备过程依据真实现场/正式包产生精确摘要，不要求用户手算或手填hash。部署执行者先生成私有0700目录`runtime/vmm-controller`，按既有owner/delegated-review记录提供`REVIEW.json`，再安全生成0600 `REQUEST.json`：

- schema=`R25_VMM_CONTROLLER_REQUEST_V1`。
- deploy：repository/ref/commit/run_id/run_attempt/archive_sha256/manifest_sha256/tool_sha256。
- review_evidence_sha256：真实已保存review记录，不伪造人类审批。

`prepare`在原shared lease内只生成固定private plan目录/sidecars；不改live config、不停业务、不迁移数据库。若watchdog/其他合法工作持锁，返回busy；必须用已记录监督协调窗口再准备，不删锁。计划中包含当时Core/AI CURRENT、原controller批准身份、配置、frontend选择、Java/Guide/Critic PID证明等。等待期间漂移应重新准备/审核，不改旧计划摘要。

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3
: "${DEPLOY_RELEASE_SHA:?actual formal release required}"
BUNDLE="$R/operations/r25-vmm-candidates/$DEPLOY_RELEASE_SHA"
: "${REQUEST_SHA256:?actual prepared request digest}"
"$PY" -B "$BUNDLE/vmm_controller.py" prepare --request-sha256 "$REQUEST_SHA256"
# 输出 UNSIGNED_PLAN.json 与准确 plan_sha256；必须先取得具体计划批准。
```

批准后以私有流程将该完整、逐字未改计划放入`runtime/vmm-controller/APPROVAL.json`，而不是生成自动批准标志。下列 `bind`自原entry暂停watchdog，在原shared lock下重新比较全部identity、保存preimage/CAS/fsync安装；不停止Java/Guide/Critic/Next。退出锁后经原entry恢复watchdog。需独立新连接验证存活；本测试不承诺生产操作时长。

```bash
: "${APPROVAL_SHA256:?actual approved plan digest}"
"$PY" -B "$BUNDLE/vmm_controller.py" preflight --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/vmm_controller.py" bind --approval-sha256 "$APPROVAL_SHA256"
```

`preflight`需取得原锁；在只读预检之外，实际监督暂停须遵循已批准协调窗口。若pause acknowledgment不明确且控制文件仍完整before状态，尝试原start恢复监督；如已有部分修改则留实际`RECOVERY_REQUIRED`并恢复同一事务。不能在混合控制文件状态盲开watchdog。

## 角色配置的独立批准修订

首次安装不要求模型依赖，也不将完整role silently启用。正式资产/私有token引用齐全后，`prepare-role`/`bind-role`提供受限第二级配置修订，而不是手改配置绕过已批准摘要。

固定private输入 `ROLE.next.json`、`SERVICE.next.json` 和 `ROLE_REQUEST.json`；request包含schema=`R25_VMM_ROLE_BINDING_REQUEST_V1`、controller_approval_sha256、review_evidence_sha256、role_sha256、service_sha256。token仅引用固定`$R/runtime/config/vmm-token`；其值不在plan/manifest/报告中。完整role仍必须enabled=false，并绑定固定service文件、service脚本/解释器摘要、有限startup/stop/restart预算。service固定127.0.0.1:5001、legacy commit、3个源摘要、原output/source/model/cache路径和8项离线资产摘要。不得引入浏览器URL/path、fixture backend或自选脚本。

```bash
: "${ROLE_REQUEST_SHA256:?actual private role request digest}"
"$PY" -B "$BUNDLE/vmm_controller.py" prepare-role --request-sha256 "$ROLE_REQUEST_SHA256"
# 审核具体输出后，逐字放入runtime/vmm-controller/ROLE_APPROVAL.json。
: "${ROLE_APPROVAL_SHA256:?actual approved role plan digest}"
"$PY" -B "$BUNDLE/vmm_controller.py" bind-role --approval-sha256 "$ROLE_APPROVAL_SHA256"
```

只修改role/service JSON和handoff内ROLE_SHA一项；保存独立revision链、before/after、review和journal。每个修订原角色必须disabled/stopped，gate CLOSED且没有DISPATCHED或未解决UNKNOWN；不写token、不清理state/ledger、不加载模型。安装后的 `vmm-enable`/`vmm-start`是另行批准动作。回滚配置前也须关闭准入、禁用并停止该角色，不可覆盖正在运行/不确定的generation。

角色配置验证只是结构/身份引用；真正启动前服务还检查可信路径、完整资产hash、解释器等。目前已记录legacy目录0777/部分权重0666会被新trusted_path fail-closed拒绝。具体私有staging或最小权限修复必须另外进入生产计划；本PR不改现场权限，不把现有宽权限当已满足安全条件。未确认资产摘要、GPU容量、依赖兼容、冷启动耗时仍需具体现场绑定/启动前验证。

## 日志与恢复

controller日志：`runtime/vmm-controller/transactions/<approval>/journal.json`，阶段`PREPARED/INSTALL_INTENT/INSTALLED/RESTORE_INTENT/RESTORED/RECOVERY_REQUIRED`。独立ACTIVE不覆盖旧Core controller ACTIVE。角色配置日志在 `role-bindings/<approval>`。

```bash
# NOT_STARTED只报告，不暂停服务或执行回滚。partial使用原审批摘要。
"$PY" -B "$BUNDLE/vmm_controller.py" recover --approval-sha256 "$APPROVAL_SHA256"
# 已INSTALLED但监督恢复失败：核验after后通过原entry恢复监督。
# 真正撤销已安装controller是显式rollback，角色须先恢复到初始disabled配置。
"$PY" -B "$BUNDLE/vmm_controller.py" recover-role --approval-sha256 "$ROLE_APPROVAL_SHA256"
"$PY" -B "$BUNDLE/vmm_controller.py" rollback --approval-sha256 "$APPROVAL_SHA256"
```

部分controller恢复前拒绝仍活着的watchdog，避免并发写；需要先用原支持入口停止并重新核验，不kill未识别进程。第三方source/config/owner/hash漂移一律拒绝覆盖；不自动新建事务、不恢复旧数据库/媒体、不删账本、不重派unknown任务。只有本次新增且hash仍为after的模块可在回滚时删除；共享锁永不删除重建。角色partial-copy故障自动恢复同一角色事务的精确前像；若漂移/in-flight阻止恢复，保留原日志并报告。

## 本轮验证边界

`test_vmm_controller.py`使用真实临时目录、文件权限、SHA、原子替换/fsync、真实flock，并在每个控制器/角色复制边界注入故障、恢复同一记录。覆盖未开始、重复、外来变更、symlink/hardlink、missing/错manifest、原应用/backup未终结、审批scope/hash冲突、正常Web/PID变化、旧POLICY不改、livewatchdog拒绝、stop ACK与start失败；明确验证运行时Core START_INTENT/backup STOPPED不会被overlay拦住，受绑定handoff/service漂移仍拒绝。一个实际shell fixture验证supervisor调用处在shared lease之外，避免重入死锁。

OS supervisor行为、正式Github审批/产物来源和生产私有布局使用明确synthetic fixture；角色进程/HTTP真实CPU测试另见`test_vmm_lifecycle.py`，Core→服务→合成WAV合同另见独立证据。没有启动生产VMM、加载权重、GPU分配或真实音乐生成。离线PASS不等同GPU/readiness/音质验收。
