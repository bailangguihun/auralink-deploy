# 社区 schema 2 与私有图片发布合同

本轮增加图文交流、结构化点评、任务/版本/复评、实际记录案例与通知所需的制品校验、数据库迁移渲染器和媒体运行目录约束。这些是待审查的代码能力，不表示已切换 AutoDL。

## 制品与运行目录

匹配 Core `community-service` 包包含原 10 个基础文件，以及 `db/002_learning_community.sql`、四个 `src/learning-*.mjs`。manifest 保留基础 `contract_version=1/database_schema_version=1`，用 `learning_schema_version=2` 表示增量。Deploy 要求完整增量清单和 `runtime_platform={os,arch,libc,sharp}`；只接受 Linux、x64/arm64、glibc/musl、Sharp 0.34.3。

Core 包生成器用锁文件和实际安装版本枚举生产依赖，省略 npm 未安装的可选平台/传递包及 `.bin` CLI shims，其他软链接仍拒绝；打包时实际解码一张图。Deploy 在受管启动前校验本机 Node 的 OS/架构/libc 与 manifest 相同。当前发布 CI 构建 Linux x64 glibc，其他平台尚未在本轮验收。

`community_lifecycle.Role` 兼容原四个配置项。schema 2 可以增加唯一的 `COMMUNITY_MEDIA_ROOT`，必须是配置文件同级的 `media` 绝对目录，已存在、当前服务身份拥有、0700且通过原有存储路径校验。文件由应用以0600写入，目录不放在只读 release 清单中，发布/回退保留该目录及所有文件。禁止静态公开挂载。schema 1 包不能借新配置开放媒体；配置摘要仍必须绑定到正式发布方案。

## 有限迁移

`community_migration.learning_schema_sql(raw, operation)` 只渲染 SQL，不连接数据库。必须匹配已审查的002原始摘要：

`5a1521f851773af9bd045499a6243220c3d3805b433d96b3db70e7d670489670`

它要求数据库恰为 `artlive_community`、现有版本恰为 `[1]`，在单个事务中 `SET LOCAL ROLE artlive_community_migrator` 后新增表列索引/版本，并写入独立 `learning_publication_receipt(operation_id,migration_sha256,schema_version)`。锁超时5秒，语句超时30秒。失败全部回滚；提交回包丢失须用 `LEARNING_VERIFY_SQL` 读取原操作回执与schema，不能盲目重跑。

迁移保留旧身份、帖子、评论、反应、画廊UUID和操作回执；不创建/替换数据库、不改密码、不导入别的项目的账号、不恢复旧全库。运行角色只增加新表 INSERT、所需列 UPDATE 和 SELECT；不给DDL、操作回执DELETE或业务表的任意删除权限。

## 正式升级前必须接通的受管入口

当前 `PackageUpgrade` 是绑定旧发布链的一次性事务，已有激活标记时会拒绝重复准备。本轮没有把它改为无限升级器，也没有提供可跳过原锁、旧证明或主机绑定的启动路径。因此，正式升级仍须为当前已安装链准备一个单独审查的有限 successor：绑定当前release/配置/控制器摘要、媒体目录身份、真实新制品SHA/run/attempt、迁移操作和共享租约，保留全部前驱证明。

推荐执行顺序供后续受管事务实现与审查：

1. 在原共享租约内读取当前链、PID身份、现有Community数据库schema和回执，确认磁盘与快照边界；不能把README中的历史状态当作当前事实。
2. 暂停原监督器的启动决策，排空确认为当前拥有的Community进程，保存前驱release、配置和运行证明。
3. 以当前身份创建并绑定私有媒体目录，显式执行一次002迁移；检查精确回执、旧记录/操作回执保持、最小权限。未知提交结果停在原操作核对。
4. 安装由真实release制品核验的schema2包与五项私有配置，经更新的原监督链启动，验证健康、能力和媒体权限。
5. 发布匹配Web，在两账号下验收发布/点评/采纳/任务/V2/复评/私有草稿/案例发布及删除后的访问撤销，再记录上线状态。

schema2保留基础合同，旧包可继续读写基础交流；回退应用须保留schema2新增数据与媒体，不执行反向DDL或恢复历史整库。回退也必须由同一受管发布方案绑定前驱，不能据此手动启动Node/Java/Next或删除锁。

## 测试边界

`test_community_learning.py` 验证增量清单、精确迁移摘要、事务边界、媒体目录绑定和启动前平台漂移拒绝；原有Community故障恢复测试继续执行。需要系统Python提供Linux `os.pidfd_open` 和 `signal.pidfd_send_signal`，缺失时属于运行环境不满足合同，不能删除校验。

原生 PostgreSQL 16 上应另外验证002实际SQL、最小列权限和旧回执保留。Web/Core本地联调、制品probe、CI通过与生产发布是不同证据；没有正式迁移回执、当前release身份及业务验收记录，不能报告上线。
