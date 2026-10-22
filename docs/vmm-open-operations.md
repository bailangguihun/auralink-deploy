# 普通 OPEN 的独立操作身份

旧 V1 OPEN 命令只绑定 role 和 gate 前像。OPEN 后没有请求就 CLOSE 时，gate 原字节恢复，因此重新独立批准仍得到同一命令摘要，被已有消费记录拒绝。这不说明模型生成失败，也不能从零 VMM 派发推断零前端/Core 提交。

## 修订合同

`R25_VMM_OPEN_COMMAND_V2` 专用于普通网站开放，独立于既有 V2 单曲 `ACCEPTANCE`。未消费的旧 V1 OPEN 也不再接受；历史消费原样保存，旧命令继续拒绝。CLOSE、UNKNOWN 核实、停止及单曲验收合同不变。

实际生产者、接收者和消费者均在 `r25/vmm_lifecycle.py`：

- `prepare_open_operation(engine, role, startup_sha)`：调用方取得原共享 lease 后生成不可变 `PLAN.json` / `COMMAND.json`，不生成批准。
- `receive_open_approval(engine, operation_sha, payload)`：接收独立终端人工输入验证后的完整批准。ACK 只表示收到，`opened=false`。
- `manage(engine, 'open')`：原管理入口取得共享 lease，完整验证批准链，再消费及关闸 CAS。生产者或接收者的成功不代表 OPEN 成功。

开放操作的身份是规范化计划的 SHA-256，不是随机 ID。计划包含：

1. 新 `ARTLIVE_WEBSITE_CLOSED_START_PLAN_V2` 的实际摘要、人工 CLOSED 启动批准和一次启动执行记录。
2. 实际 FIRST_READY、完整只读 READY 证据、boot/PID/start ticks、role，以及已安装三模块和全部工具摘要。
3. 原 900 秒绝对启动期限、首次 READY 起 600 秒的单调钟与墙钟批准期限。新操作不能重新起算 FIRST_READY。
4. 当前 gate 前像、完整命令/窗口/开放消费记录摘要。
5. 从真实 `open_operations` 推导的下一序号和上一已消费操作摘要。
6. 此次独立开放的完整运营范围。

计划保存在私有 `runtime/vmm-open-operations/<operation-sha>/`。字段不允许任意增补；序号、前驱和消费前像都由消费者重新验证。相同未消费状态重复 prepare 只返回同一计划，不产生新权限。新独立批准严格绑定 operation SHA 和规范化 command SHA；音乐批准不能替代 OPEN 批准。

消费时先把 command SHA 与 operation SHA 一起持久写入原 lifecycle 记录，再改 gate。消费后中断、门锁超时或批准到期，消费仍保留，禁止重放或退款；确需另开只能重新准备并独立批准下一操作。消费者在 fsync 后和 GateStore 持锁期间再次检查原期限及 gate 前像。改变命令空白不能绕过 operation 消费。

同一真实 READY 进程在**原 600 秒批准期限内**，无请求关闭后可另批下一操作。超过该期限不允许以重开重置时钟，后续运行仍须新的具体启动授权。停止/禁用/新启动不会清掉任何开放消费。存在 in-flight 或未核实 UNKNOWN 时不生成新开放计划；UNKNOWN 仍须原核实程序处理，不自动重放生成。

## 运营范围没有改变

当前范围固定为一次开放、30 分钟人工值守，所有正常认证且满足 Core 工作流/来源条件的用户可能生成。实际 duration 3–30 秒、生成并发 1、无自动重试保持。OPEN **没有内建账号白名单、自动到期、总请求额度**。600 秒是批准并执行 OPEN 的期限，不是运行自动过期机制。运营者仍负责关闸、检查 Core/VMM 在途与 UNKNOWN、排空后停止并禁用。不能把辅助计时工具称为服务原生 TTL。

## 正式安装与后续工具绑定

这是三个模块和关联摘要的受管更新，不发布 Core/AI/Web；从已 INSTALLED 模块修订升级见 [module revision](vmm-module-revision.md#已-installed-版本的后继更新)。旧模块的安装证明、批准、消费和 UNKNOWN 不变。

正式安装后才可复制现有独立 OPEN 桥、生产者、接收器、执行器和 observer，并绑定实际新 role/模块/工具完整摘要。旧启动计划不符合新 schema/limits，不能复用。本 PR 不伪造未来生产工具摘要、安装批准或启动授权。

新工具必须调用上述生产者/接收者 API，使用实际 `StartupPlan` 的 `installed_root`、`role_sha256`、`installed_code_hashes` 和私有 window/tools 副本；limits 精确等于源码 `OPEN_STARTUP_LIMITS`。原启动材料路径为 `runtime/vmm-website-windows/<startup-sha>/`，固定文件由真实启动观察产生：`STARTUP_PLAN.json`、`STARTUP_APPROVAL.json`、`EXECUTION.json`、`FIRST_READY.json`、`READY_EVIDENCE_COMPLETE.json`、`READY_GATE.raw.json`。不得合成 READY 或借用旧进程证据。

独立输入必须明确同意当前 operation 与 command 以及计划中的运营范围，然后接收器提供：

```json
{
  "schema": "ARTLIVE_HUMAN_OPEN_OPERATION_APPROVAL_V1",
  "operation_sha256": "实际新计划摘要",
  "command_sha256": "实际命令摘要",
  "scope": "ONE_NORMAL_WEBSITE_OPEN",
  "authorized": true
}
```

这只是接口结构，不是批准文件或可执行许可。实际命令原字节送入原 `runtime/config/vmm-gate-command.json`，外部精确 SHA 经现有 `vmm-open --vmm-command-sha256` 管理入口传入；不得另启 Web/Core 请求或直接调用公开生成端点。需要新的真实人工批准后才能执行。

## 离线验证边界

`test_vmm_open_operation.py` 使用临时私有目录、真实 CPU HTTP 子进程、/proc 身份、flock 和 fsync，执行实际生产者→独立接收器→manage/GateStore。验证无请求重开、旧/已消费操作、错误批准、前像漂移、期限跨越、UNKNOWN、停启后记录保留。`test_vmm_module_successor.py` 在真实安装后的模块字节上执行此链，并验证升级失败恢复及旧证明保留。未加载模型、未进行生产业务写，也没有把隔离结果当作生产更新或网站生成验收。
