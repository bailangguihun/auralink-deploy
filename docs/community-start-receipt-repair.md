# Community 启动回执修复

适用于已安装的 Assistant 插图版及其 Core 后继控制器。只有已提交的 Community 代码包处在 `START_DECISION`，最后决定缺少进程回执和日志，最近真实进程已终止，且还有连续重启预算时，才可以用这个入口恢复。

## 审核与执行

1. 从受保护的 Deploy `release` 下载正式 Community 控制包，核对包和 manifest，解包到以 manifest 摘要命名的候选目录。保留现有 watchdog 使用的正式包。
2. 在当前 Community 安装的 `start-receipt-repairs/reviews/<审核摘要>.json` 放置 0600 的审核证据。核对活动安装、终止回执、缺失操作 ID、预算、监听端口和数据库迁移回执。
3. 调用候选包中经验证的 `community_host_cli.py`。传入原有 `--plan`、`--plan-sha256`、`--bundle-manifest-sha256` 和 Knowledge、Supervision、Studio、Reference、Code、Assistant rollout 绑定。额外传入 `--receipt-controller-sha256 <新控制包的 manifest 摘要>`，再执行对应子命令。

| 子命令 | 附加参数 |
| --- | --- |
| `receipt-prepare` | `--operation-id <缺回执的操作 ID> --review-sha256 <审核摘要>` |
| `receipt-repair` | `--package-plan-sha256 <批准计划摘要>` |
| `receipt-recover` | `--package-plan-sha256 <同一批准计划摘要>` |

prepare 在原共享锁中只读核查生产状态，保留监督文件原字节，并生成带新操作 ID 的 `UNSIGNED_PLAN.json`。审查实际计划后，按原字节保存为同目录的 `approval.json`。

repair 核对原证据，记录独立 journal，保留旧启动历史及预算起点，使用现有 Community 托管入口为本次修复启动一个新操作。进程归属、健康和数据库迁移回执通过后记录 `COMMITTED`。旧操作的缺失回执保留为缺失证据。数据库和业务配置只读校验。

如果执行中断，recover 可继续明确未开始的步骤，或观察已经具有真实回执的新操作。若新启动也丢失回执，恢复会停在 `START_INTENT` 并保留证据。上线后检查 Community 的本机健康、托管进程及与 Core 的集成。
