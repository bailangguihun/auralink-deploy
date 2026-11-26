# Assistant 插图版 Core 后继发布

本入口把一个已审核的 Core Actions 制品绑定到已经提交的 Assistant 插图版 R25。原 Core 是恢复目标。Critic 和 Guide 沿用已提交的 AI 事务及其代码、配置和依赖。Community 沿用原数据、进程身份和监督记录。新版 Web 在新 Core 提交后使用同一制品发布，Critic 启动同时校验 Web 的提交、构建标识、制品摘要和重新切换的回执。回退新 Core 前，先用托管入口恢复原 Web。

## 准备输入

通过受保护的 Deploy `release` 生成 Core 控制器、VMM 和 Community 三个同一运行的官方包。按现有 R25 安装目录放置三个包和各自 manifest，并将新 Community 模块放在其 manifest SHA 命名的目录下。保留旧包。核对同一个 Core Actions 运行的 JAR、候选 manifest 和验证报告，再存放到 `core_publish.py prepare-incoming` 返回的目录。

在 Core 发布存储的 `assistant-rollout-successor-requests/<请求 SHA-256>/` 写入 0600 的 `request.json` 和独立的 `review.json`。请求须含以下字段。

| 字段 | 内容 |
| --- | --- |
| `schema` | `R25_ASSISTANT_ILLUSTRATION_CORE_SUCCESSOR_REQUEST_V1` |
| `deploy` | 官方 Core 控制器包的 `repository`、`ref`、`commit`、`run_id`、`run_attempt`、`archive_sha256`、`manifest_sha256`、`tool_sha256` |
| `vmm`、`community` | 同一次 Deploy Actions 运行的对应包信息，字段同上，省略 `tool_sha256` |
| `core` | 目标 Core 的 `commit`、`run_id`、`run_attempt`、`artifact_sha256`、`report_sha256`、`manifest_sha256` |
| `web` | 已完成发布验证的候选回执使用的 `commit_sha`、`github_run_id`、`github_run_attempt`、`archive_sha256`、`build_id`、`selection_sha256`、`reference_sha256`、`receipt_stamp`、`receipt_sha256` |
| `review_evidence_sha256` | 私有审核记录 `review.json` 的摘要 |

`receipt_stamp` 是旧 Web 至候选 Web 的已提交切换目录名后缀。准备阶段的线上选择必须是原 Web。候选回执、选择和引用文件均应留存。新 Core 提交后，使用同一 Web Actions 制品再次发布，以取得时间晚于 Core 提交的新回执。

## 安装和验收

1. 从已核验的 Deploy 控制器候选目录运行 `assistant_rollout_prepare.py prepare-successor --request-sha256 <请求摘要>`。它通过现有共享锁读取安装状态、制品和回执，生成 `UNSIGNED_PLAN.json`。原 Core、AI 必须都已提交，且当前兼容性和服务身份能通过预检。
2. 走现有私有计划审核，将批准的原始字节保存为同目录的 `approval.json`。使用 `assistant_rollout_prepare.py install --approval-sha256 <批准摘要>`。安装要求现有 Community 返回 `READY_PRESERVED`，并确认原监督器、服务身份、控制文件和事务回执。遇到缺失启动回执，应沿现有 Community 恢复流程处理。
3. 在 controller journal 为 `INSTALLED` 后，放行已绑定的 Core Actions 运行。查验原 Core 发布器的事务是否 `COMMITTED`，再用托管 Web 发布入口切换已绑定的制品。分别查看 Core 与 Web 回执、线上版本及服务健康。
4. 如果控制器安装中断，保留 journal，执行 `assistant_rollout_prepare.py recover --approval-sha256 <同一摘要>`。恢复仅接受计划记录的前后镜像。应用发布开始后，使用现有 Core/Web 恢复入口及其各自事务回执。

## 对已经提交的后继版本再发布一次

现有后继版本的 Core 和 Web 均须已提交并通过当前托管校验。使用新一轮经过保护分支审核的正式 Core、Web 和 Deploy 制品，按上面的流程重新准备与审核。第二轮的回退目标以当前已提交的 Core 和 Web 为准。此前的 Core、Web 发布回执仍按原摘要保存。

仍使用 `prepare-successor`。准备过程从上一轮已安装的审批记录读取 AI 快照原来绑定的 Core 身份，将该身份和上一轮的 AI 日志、快照摘要写入新计划并校验。AI 和 Community 的现有身份仍需通过原有保护校验。已安装的兼容生命周期文件按原摘要保留。

新的 Core 或 Web 制品仍须分别绑定到本轮审批。这个入口覆盖首次后继发布和一次已提交后继版本的续发。
