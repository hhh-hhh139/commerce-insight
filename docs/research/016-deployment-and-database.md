# 可部署版本、业务数据库与免费展示

日期：2026-11-08。状态：已调研、选择实施路线；尚未声称容器验收或公网运行。

## 当前问题

业务数据库为 H2 文件，启动时反复执行 `schema.sql`，没有生产数据库配置与迁移历史。脚本依赖项目内 macOS `Contents/Home` 工具链，尚没有干净 Linux 环境证明。Python checkpoint 与模型累计账本在 `.local`，丢失它们可能丢恢复状态并破坏费用连续性。用户目前没有云资源，要求先可部署、公网尽量免费。

代码已观察到 H2 专属 `CLOB`、`MERGE ... KEY`；此时没有声称已经复现 PostgreSQL SQL 错误，真实新数据库检查安排在工具链准备后。没有容器运行证据，也不能把写好 Dockerfile 当部署完成。

## 同类与官方资料

均于 2026-11-08 访问；未特别注明的页面为滚动文档。

| 来源 | 支持的事实与借鉴 |
| --- | --- |
| [Metabase 应用数据库](https://www.metabase.com/docs/latest/installation-and-operation/configuring-application-database) | 区分应用元数据与分析数据；本地 H2 演示，部署推荐 PostgreSQL。借鉴分工，不把其推荐直接当本项目性能证据 |
| [Spring Boot 4.1.2 初始化](https://docs.spring.io/spring-boot/how-to/data-initialization.html) | 同一数据库用一种 schema 管理机制；支持 Flyway PostgreSQL 模块。生产配置禁用 basic SQL init，使用版本迁移 |
| [PostgreSQL 18.6 发布说明](https://www.postgresql.org/docs/release/18.6/) | 固定稳定小版本，官方列出修复；不采用 19 RC |
| [PostgreSQL 源码安装](https://www.postgresql.org/docs/current/install-make.html) | 可指定安装目录，用非全局工具链在本项目验证真实数据库 |
| [PostgreSQL 备份](https://www.postgresql.org/docs/current/backup.html) | 区分逻辑 dump、文件备份和持续归档；本项目先做停写逻辑 dump＋应用状态备份/恢复，不声称 PITR |
| [Docker Volumes](https://docs.docker.com/engine/storage/volumes/) | volume 生命周期独立于容器；业务库和应用状态都必须持久化 |
| [Docker 多进程](https://docs.docker.com/engine/containers/multi-service_container/) | 默认建议按关注点拆服务，也允许 wrapper 管理多进程；必须正确停止/回收子进程 |

## 方案选择

1. 只给 H2 文件挂盘：变动少，但不能验证 SQL 跨库、迁移和标准数据库运维，且原计划的业务部署存储没有完成。
2. 全部状态一次迁至 PostgreSQL并拆多个独立服务：便于后续多副本，但现在单空间、有限并发，不必同时改 checkpoint、工具缓存、费用存储和服务鉴权。
3. **选择：PostgreSQL 业务库＋应用持久卷；单实例应用容器＋独立数据库容器。** H2 留作零外部依赖的本地演示。生产 profile 使用 PostgreSQL/Flyway，H2 profile 保持现有初始化机制，不在同一个数据库同时开两套迁移。Java 和 Python 保留进程及职责边界，第一版复用既有启动器管理两者生命周期；内部通信继续回环，减少为容器化改动鉴权的风险。

代价：这是单实例个人/试用部署，不支持水平扩容或高可用。SQLite checkpoint、累计预算文件仍要求同一持久卷、单写者和备份，不能跨节点共享或放在对象存储挂载上假装本地事务。出现多副本/多店隔离需求时，再迁 PostgreSQL checkpointer、事务预算与独立 Worker 身份；不提前承诺性能收益。

Java SQL 查询保留参数化与现有事务/租约约束，实际检查 PostgreSQL 下的大小写标签、布尔值、唯一键、锁与并发行为，不能只通过 H2 PostgreSQL 兼容模式就宣称支持。

## 免费公网选择

| 方案 | 官方限制 | 决策 |
| --- | --- | --- |
| [Render Free](https://render.com/docs/free) | 15 分钟无流量休眠；重启/休眠本地文件丢失；免费实例无持久盘；免费 PostgreSQL 30 天到期；官方不建议生产用途 | 不作为原样持久化应用的长期免费部署承诺 |
| [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview)、[存储](https://huggingface.co/docs/hub/spaces-storage) | 当前 Docker/Gradio 计算 Space 创建需相应付费计划；文件系统默认临时；静态 Space 免费 | 不沿用旧文章“免费 Docker Space”的说法，也不购买计划 |
| [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) | 静态站点托管，不能运行本项目 Java/Worker 后端 | 个人公开仓库配套免费静态报告/操作演示可行，必须标注非实时后端；完整功能通过自部署运行 |
| 自有个人机器/服务器＋Compose | 软件可开源运行，硬件、网络、域名不保证免费 | 提供实际验收后的部署说明；用户暂没有资源，当前不购买、不声称已公网上线 |

## 实施与验收顺序

- 保存源码快照，项目 `.local` 内准备真实 PostgreSQL（官方源码和 SHA），不安装系统服务、修改全局配置、连接既有数据库或 Docker daemon。
- 先建立 PostgreSQL 与当前 schema/驱动的基线，再增加生产 profile、Flyway 版本迁移，验证新库/重复启动、失败迁移、并发幂等、租约与导入不可变版本；保留原 H2 回归。
- 固定依赖及镜像版本，准备干净运行入口、非 root 权限、健康检查、退出传播与持久卷。容器必须实际构建和启动验收；本机不能安全运行时在核对后的个人 GitHub CI 验证，不碰公司容器。
- 停写备份数据库＋应用状态，恢复到独立空目录/库，核查报告/数据版本/预算历史并继续任务；拒绝覆盖非空目标。备份含用户数据和应用凭据，只留个人忽略目录。
- 反向代理/TLS 和 owner 会话联验；代码/数据许可、发布内容脱敏、整体对比、个人 GitHub 与免费静态展示随后完成。

付费模型目前受阶段 4b 未知用量限制，这不妨碍上述数据库、部署、恢复和离线任务验收。不得将部署检查写成新退款能力或多 Agent 真实模型验收。

实施前归档：`.local/baselines/before-postgres-20261108/source.tar.gz`，306 文件，SHA-256 `719cecf86713e882089f25348681821258a7f0e3bab5f2f993a67a22804c0e43`，成员及逐文件 SHA 均核验；源代码基线见 `../probes/deployment-before-2026-11-08.json`。PostgreSQL 官方源码 SHA-256 固定为 `555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f`（18.6 tar.bz2）；本机工具链仅在本项目编译，Linux 部署镜像另行验收。

## 5b 备份与权限的进一步选择（2026-11-08）

5a 已通过真实业务数据库检查；备份模块改动前使用 `after-postgres-20261108` 的 317 文件源码归档，SHA-256 `ad65b2b0e49bb3acfa9352d0d55141cf84b6012927aea39126651e68d6f73b20`。此时没有备份入口，且状态接口仍写死 H2；不能把它们描述成发生过数据丢失事故。

补充依据：[Metabase 备份](https://www.metabase.com/docs/latest/installation-and-operation/backing-up-metabase-application-data) 将自托管 PostgreSQL 的备份交给原生数据库工具；[pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html) 提供一致的数据库导出，但不会同时备份外部应用文件；[pg_restore](https://www.postgresql.org/docs/current/app-pgrestore.html) 支持单事务恢复；[schema 权限](https://www.postgresql.org/docs/current/ddl-schemas.html) 区分数据库权限与 schema 对象创建权限。均为当日访问的滚动官方文档。

选择停写备份，而非直接复制活跃 SQLite 文件，亦不为单实例引入跨库分布式快照。启动器、直接 Agent 执行和密码设置持有共享状态锁；维护入口独占同一锁，并检查目标 PostgreSQL 无其他连接。范围是本项目受管启动入口，不能代替对任意外部数据库客户端的隔离。备份含原生 custom dump、登录哈希/内部 token、Agent runs 和所有累计预算 JSON；排除日志、工具链和测试产物。只向不存在的应用目录和空库恢复，按文件集合/SHA/代码版本验证；恢复失败留下禁止启动的标志。SHA 用于检验完整性，不提供作者认证；只接受自己的可信备份，不能导入陌生 SQL dump。

备份前停止应用意味着有维护窗口，RPO 是备份时刻，不支持 PITR/热备。恢复后不能同时运行旧实例，避免两个账本副本分别消费。涉及模型消费的灾难恢复，应以最新预算记录/供应商用量对账为前提，不能利用旧备份回退累计费用；本次只复制原账本到隔离测试目录并核对字节，不修改或结算原未知预占。

部署账户选 schema owner：`NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`，仅连接专用库并拥有 public schema 以执行 Flyway。它仍能修改自身业务 schema，不能称作仅 DML 的最小权限账户。拆独立迁移身份/DML 身份留给多管理员或更严格环境；现阶段避免为单实例增加另一套长期秘密。集群管理员只用于初始化，不传给 Java/Worker。

验收预先要求：已确认外部合成数据、报告和排队任务迁移后可用；中断的真实 LangGraph checkpoint 恢复时不重复工具；预算和 owner 哈希/token 字节一致；运行中备份、非空目标、损坏和符号链接被拒；业务账户不能创建库/角色或执行服务器程序。状态页必须区分 H2/PostgreSQL。

## 容器交付补记（2026-11-08，待干净 Linux 验收）

前置归档 `after-tls-20261108`：336 文件，SHA-256 `d59555f3602072f1b08555c9477c1639f36711591b3457d2faa6bcff7eeb47e7`。官方镜像文档确认 Python `3.12.16-slim-bookworm`、Temurin `21.0.13_9-jdk-jammy`、PostgreSQL `18.6-bookworm`、Caddy `2.11.9-alpine` 标签；本机对匿名 registry 的连接仍超时，尚没有可信镜像 digest 或实际镜像构建证据。后续只能从干净个人 CI 的真实解析结果固定 digest，不能编造。

设计：多阶段构建、Java/Python 业务分工保留，复用已验收启动器管理进程；独立 PostgreSQL；可选 Caddy 公网 profile。应用 UID 1000、只读根文件系统、独立 owner/agent/control 持久卷、临时 Worker 文件 tmpfs、容器资源和日志上限。应用/数据库无宿主端口，只有选用代理的部署提供 HTTPS。默认离线且应用网络无出站，不传入模型 Key。容器和宿主工具链分开，构建上下文白名单排除 `.local`、原始数据与既有金融项目。

根据 [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) 与 [Spring Boot configtree](https://docs.spring.io/spring-boot/reference/features/external-config.html#features.external-config.files.configtrees)，数据库密码作为显式文件挂载给对应服务，Java 直接读取，不经 Python argv 或 Worker 环境。Compose 的本地文件 secret 不是远程秘密管理服务；为非 root 容器读取，生成的数据库秘密文件可读但宿主父目录 0700，不能因此公开宿主父目录。模型 Key 仍不落盘，不混入此机制。

依据 [PostgreSQL 官方镜像](https://hub.docker.com/_/postgres)，18 及以上持久卷挂在 `/var/lib/postgresql`，不能照抄旧版 `/var/lib/postgresql/data`。数据库初始化角色与业务账户分离。控制锁也位于独立持久卷，确保维护容器与应用看到同一把锁。

干净 CI 只面向核对后的个人公开仓库，使用官方固定 commit 的 checkout（不持久化 Git 凭据），`contents: read`，无个人/公司 secret，无付费模型；先构建并跑现有离线契约，再用新卷启动 Compose、验证 TLS/登录/业务任务与重启持久化。每次运行记录源码 commit、镜像 digest、版本、原始结果。CI 配置存在本身不等于已通过，完成后补实际链接和结果。
