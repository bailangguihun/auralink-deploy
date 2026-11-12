# 原版社区 2.0 的 schema 3 发布边界

本轮配合 Web 的原版 DOM/CSS/JS 移植和 Core 的异步 PostgreSQL 帖子/房间移植，准备 schema 3 的制品与迁移合同。生产未执行本轮迁移、安装或激活。本文不是生产执行批准，也不把本地运行、源码合并、包校验或候选 SQL 当成上线证明。

来源为用户提供的本地 `国画社区系统2.0/国画社区系统2.0`。七个原文件的 SHA-256 清单见匹配 Web `docs/community-v2-reference.md`；后端来源与 UUID 兼容投影见 Core `community-service/docs/reference-v2.md`。原目录、SQLite 数据、密码、默认管理员和演示互动均不导入或修改。

## 本轮制品能力

[community_bundle.py](../r25/community_bundle.py) 保留 `contract_version=1`、`database_schema_version=1` 和 `learning_schema_version=2` 的原基础合同，新增 `reference_schema_version=3`。schema 3 必须同时具有 schema 2 增量声明，且包含以下六个文件：

- `db/003_reference_community.sql`
- `src/v2/database.mjs`
- `src/v2/tables.mjs`
- `src/v2/posts.mjs`
- `src/v2/rooms.mjs`
- `src/v2/gateway.mjs`

Core 包生成器按锁文件枚举实际生产依赖、保留所有 source/migration 摘要并执行真实 Sharp 解码。schema 2 旧包仍绑定 Sharp 0.34.3；schema 3 包绑定 Sharp 0.35.5，Core 另锁定 ws 8.22.2。manifest 继续绑定 Linux、架构、libc、Node 最低版本、release SHA、run/attempt 和逐文件清单。不能把不同本机平台的 native 包直接替换使用。

Core 的 `community-package.yml` 仍仅产出制品，不 SSH、不执行 SQL、不安装或启动服务。Deploy 的包清单识别能力不是生产升级入口；实际制品还须来自受信正式发布流程，不能用本地伪造的 provenance 代替。

## schema 3 迁移要求

Core 的显式 `003_reference_community.sql` 以当前 `[1,2]` schema 为前驱；运行服务不能自动建表。新增原版业务的 `v2_*` 表及幂等账本，将既有 UUID 帖子/评论/通知映射到 `legacy_id` 兼容投影，并用触发器维持基础内容和互动同步。既有账号、画廊 UUID、成长任务/版本/案例、媒体和旧操作回执必须保持。

迁移还明确支持纯媒体帖及有标识的 AI 展示身份。这不是替换 Core 账号体系；原版密码列不保留，平台角色由 Core 的认证身份接口提供。原版 `server.js`/`room-feature.js` 的安全漏检修正属于服务合同校验，不改变页面布局。

[community_migration.py](../r25/community_migration.py) 已增加 `reference_schema_sql` 和 `REFERENCE_VERIFY_SQL`。最终 003 SHA-256 固定为 `35aa201028886c85a335b3c6ebf58d8a79ccb3c7d6cd4e7e4261e879faa35a9f`。渲染器要求数据库为 `artlive_community`、前驱恰为 `[1,2]`、操作 ID 合法，使用迁移角色、单事务、5 秒锁超时和 60 秒语句超时；添加 `reference_publication_receipt` 和最小运行权限。操作账本不授予 DELETE，墨币流水只增不改。丢失提交回包后通过验证 SQL 读取原操作回执、schema 和权限，不盲目重跑。

渲染器只返回 SQL 字节，不执行迁移、不切换进程或激活标记。原生 PostgreSQL 最小角色测试已经完成，但当前没有绑定已安装生产链的 schema 3 有限迁移执行器。不能复用只处理 002 的 `learning_schema_sql`，也不能因 renderer 和包校验存在就直接上线。

旧 `community.operations` 与新 `v2_operations` 是两套保留的回执。后者记录 `RUNNING/COMMITTED/FAILED`，未观察到完成不是重新提交 SQL 或业务请求的授权。未知迁移结果必须先读原操作回执和当前 schema，不恢复旧全库、不重建账号、不清除业务历史。

## Web 和运行配置前置条件

新 Web `/community` 直接使用原版 v2 API，不静默回退旧 schema UI。因此生产切换 Web 前必须完成：

1. 匹配 Core JAR 提供认证 `GET /api/user/community-identity`，实际角色来源与旧 JWT/账号限制通过验收。
2. 当前安装链的受管 Community 已显式应用 schema 3，并保留旧 UUID/成长记录；私有 `COMMUNITY_MEDIA_ROOT` 及其 `reference` 子目录具有正确归属和权限。
3. server-only `COMMUNITY_INTERNAL_ORIGIN` 指向准确 loopback Community，Web BFF `/api/community/v2` 可转发对应 `/api/v2/community`。
4. Node middleware 的同源 `/api/community-ws` 能将真正的 upgrade 和 socket 数据转交 loopback `/ws`。HTTP 200 或健康检查不能证明升级链路可用。
5. 媒体不透明会话和 30 秒单次 WS ticket、来源检查、账号切换及资源 ACL 在匹配 release 上实测，然后才切换新 Web。

Community 仍使用独立 runtime DB 身份、原有 loopback Core/Web/端口配置和私有媒体根。房间上传、旧媒体与付费作品不应变成静态公开路径；文件必须保留在发布清单外的受管数据目录。源码支持的提供商 Key 不能自行绕过生命周期环境白名单；服务端提供商配置和实际外部调用若要上线，需另纳入明确合同。

本地 3000/5000/3178 端口和真实数据副本克隆只是开发运行配置，不是服务器安装路径、发布目标或生产验收。真实密码、Key、cookie、数据库、截图和媒体不进入代码仓库。

## 现有一次性发布链与有限 successor

当前 `PackageUpgrade` 是绑定旧安装链的有限一次性事务，存在激活标记时不能重复准备。本轮的包清单/schema 渲染能力不等于它已成为通用 schema 3 升级器，也没有新的生产迁移、受管启动或激活实证。

后续正式执行仍需单独审查的有限 successor：精确绑定当前 release、控制器/配置摘要、原共享租约、当前拥有的 Community 进程、私有目录、正式新制品、迁移 ID/摘要和前驱证明。持原共享租约排空当前进程，再做受控迁移、配置和启动，并保留失败尝试与恢复证据。禁止删除锁、手工拉起 Node/Java/Next 或用旧 first-install 方案覆盖当前安装。

回退必须保留 schema 3 新增的房间、门票、墨币流水、AI 草稿及旧成长数据和所有媒体，不执行反向 DDL 或恢复历史整库。回退到旧应用是否仍可写、触发器和权限是否兼容，须在具体 successor 中验收，不能仅由旧健康合同推定安全。

## 验证分层与待验收项

| 层级 | 当前证据及限制 |
| --- | --- |
| 源码移植 | 原七文件摘要可核对；DOM ID、样式与交互对应；安全/身份/数据库适配单列 |
| Core 合同 | 服务全套与新增安全回归以 `community-all-final.log` 为准；实际 Core 身份边界八项已通过，模拟模型结果不等于真实付费调用 |
| 本地整体运行 | 独立克隆的真实数据、实际 Core 和浏览器双账号功能已运行；原副本保持、无生产访问 |
| HTTP/WS/手机 | 已验证同源 WS、语音收听/虚拟麦克风及 390px 无横向溢出；最终交接/竞态实测由验收记录更新，真实跨网络音频未验 |
| Web 构建 | 最终隔离生产构建、Lint 和 265 项测试通过；不替代最终浏览器交接验收 |
| 原生迁移 | 固定 003 摘要的隔离原生 PostgreSQL 最小 runtime 角色七组合同通过；不是生产迁移回执 |
| 本地包夹具 | 确定性生成、native 解码及 13 项负测已完成；最终文件数/大小/摘要以 `PACKAGE_FIXTURE_RESULT.json` 对应快照为准，非 release |
| Deploy 合同 | `deploy-final-tests.log` 记录 102 项通过；没有因此执行生产安装或激活 |
| 正式发布 | 有限 successor、受信制品、迁移回执、当前 release/进程身份及业务验收仍未形成生产闭环 |

钱包、任务和收益 GET 保留原版懒建钱包/结算逻辑，会改变本地数据库；打开社区的测试不能归类为数据库只读。原版功能代码存在，也不等于所有成员治理、门票/七天/退款、私有作品、共享/断线/重启或真实麦克风路径都已覆盖。

旧链接、画廊引用、按账号草稿、作品成长及与平台助手的手动草稿交接已接入；房间导航、资产 ACL 和回执修正的最终实测状态由验收记录更新。原版页面没有帖子编辑按钮，后端 PUT 保留，作品编辑沿用成长对话框。当前没有“全部功能已验收”或“生产已部署”的结论。
