# Community 启动次数耗尽后的受控恢复

旧 Community watchdog 将整个安装生命周期的启动次数限制为三次；长时间正常运行后的主机重启也会消耗次数。三次均曾 READY 的安装最终仍会进入 `STOPPED / RESTART_BUDGET_EXHAUSTED`。此修复只替换监督策略，不重建服务、不迁移数据，也不清空旧记录。

## V2 策略

`RecoverySupervisor` 使用独立 `supervision-v2.json`。旧 `watchdog-state.json`、三个旧运行记录及日志保持原字节，并绑定在升级批准中。新的启动决定先持久化到追加历史，再交给原 `Role` 启动；丢失启动回执时保持 UNKNOWN，不能重放。

连续三次启动后仍未稳定健康时停止。只有同一受管进程持续通过健康观察至少 60 秒才恢复自动重启额度；一次 READY 不恢复额度。启动历史不删除。真实重启后的旧进程由原 boot/PID/start-ticks 校验处理。显式 `enable --operation-id <32 hex>` 只允许已停止状态，并记录幂等恢复事件，不直接启动进程。日志上限、停止、进程归属和原部署租约检查继续生效。最多保存 16384 次新启动决定；达到审计容量时拒绝继续启动。

## 控制器升级边界

该有限升级仅支持原 Knowledge publication 已安装、原 Community 安装已登记且旧三次额度耗尽的状态。新的 Deploy release 必须同时提供原清单规模的 VMM 四文件、controller 二十五文件和 Community 七文件产物；三个产物来自同一次正式 release 工作流。

`prepare-community-supervision` 在原锁下生成 `UNSIGNED_PLAN.json`，绑定源产物、review SHA、原锁 device/inode、Core/AI 当前记录、原 Community 安装和旧日志、三个目标文件的前后 SHA。只有经过审核的完全相同字节可成为 `approval.json`。所有 CLI 输入来自固定根目录，不能传任意执行路径。

`install-community-supervision` 暂停原 watchdog，在原共享租约下保存前后像与 INSTALL_INTENT，仅 CAS 替换：

- `operations/r25-publisher/vmm_controller.py`
- `operations/lifecycle_adapter.py`
- `runtime/handoff-control.env` 中上述两个文件的 hash

Core policy/CURRENT/事务、原 guard、VMM 状态、服务包/配置和原 Community Host approval 不改。现有 Knowledge 证明链通过精确绑定的三份前像验证。新生命周期选择器只有在新 marker、批准、INSTALLED 回执及父批准一致时才选择新的 Community CLI。安装器不启动 Node；提交后恢复原 watchdog，由原周期接管。

写入控制文件之前失败且原前像仍完整时恢复旧 watchdog。部分写入后保持暂停，必须执行 `recover-community-supervision` 按原批准向前完成；不得重置历史、删除锁、回滚已提交升级或手动启动 Node。已提交但调用回执丢失时只验证现状并恢复 watchdog，不重建 V2 状态、不重复启动。生产操作须保存原失败输出及每一步观察。

## 验证

`test_community_recovery.py` 使用真实隔离 Node 进程覆盖超过三次正常恢复、连续失败上限、短 READY、旧 boot、幂等恢复、丢失启动回执、归属检查和旧记录不变。`test_community_recovery_upgrade.py` 复用完整 Knowledge 前驱证明链及真实清单、文件 CAS，覆盖旧证明保留、锁替换、输入漂移、写入前失败、部分写入续行及提交回执丢失。

上线前需完成仓库 CI 和正式 release 产物核对。上线后验证 V2 状态、原进程归属、至少 60 秒健康观察、原记录 SHA 保持，以及经过现有 Web 代理的真实社区读取。不要以主站健康或匿名页面 HTTP 200 代替社区接口验收。
