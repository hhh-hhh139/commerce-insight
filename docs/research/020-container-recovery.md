# 容器备份恢复：维护容器与空卷验收

日期：2026-11-09。状态：已决策、实施中；尚无容器恢复通过结论。

## 问题和基线

阶段 5f 已证明 Linux 容器的业务任务与重启报告保留，但未演练备份恢复。现有备份程序在 macOS 真实 PostgreSQL/SQLite 组件中通过；应用镜像没有运维入口。另一个已复现问题是：新建 volume 的挂载根目录天然存在，原 `restore()` 对任何已存在目标都拒绝。用真实空目录调用原函数，在读取备份或连接数据库之前得到 `restore_state_target_exists`。

可恢复源码基线：`f238c19580f11faed761ff3d274ff9fd6bffc02e`，tree `f509c3c271ab9e51cdb7b26d6a0a39c70f42358e`。文件 SHA 与反例记录位于 `.local/container-recovery/before.json`。本机不访问任何 Docker daemon；容器部分继续只在已核对的个人 GitHub hosted runner 运行。

## 调研与选择

以下为本日访问的一手来源；网页和官方镜像 Dockerfile 为滚动版本：

- [Metabase 备份](https://www.metabase.com/docs/latest/installation-and-operation/backing-up-metabase-application-data)：自托管 PostgreSQL 交给数据库原生备份。它的应用状态在单个 SQL 库，本项目另有 checkpoint 和预算，不能仅备份业务库。
- [PostgreSQL pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)：逻辑备份可移植，custom archive 配合 pg_restore；旧主版本客户端不能导出更新主版本服务器。
- [Docker volume 备份](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes)：维护容器可以挂载数据卷。直接打包卷并不自动保证跨数据库/应用文件的一致性。
- [Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)：按需维护服务可通过 profile 显式执行，不随正常业务启动。
- [官方 PostgreSQL 18 Bookworm 镜像配方](https://raw.githubusercontent.com/docker-library/postgres/master/18/bookworm/Dockerfile)：复用同版本原生客户端，实际镜像 digest 由 CI 解析并保存。

| 方案 | 收益和代价 | 结论 |
| --- | --- | --- |
| 原样复制数据库卷和应用卷 | 操作少，但不能直接证明 PostgreSQL 一致性、文件完整性和预算连续性；物理格式耦合版本 | 不选作首版恢复机制 |
| 在业务镜像安装 PostgreSQL 运维工具 | 操作简单，但常驻业务环境增加无业务用途的工具与依赖 | 不选 |
| 独立、按需运行的维护镜像 | 复用 pg_dump/pg_restore 与已有清单、状态锁、指纹校验；需要维护一个小入口 | 选择。基于同版 PostgreSQL 镜像，仅补标准库 Python 来运行维护程序，不运行 Agent |

空卷适配比较：把状态整体迁到新路径会扩大业务改动；在卷内中转后搬运文件会增加恢复完成与搬运之间的失败窗口。选择仅给 `restore()` 增加显式的“允许已存在但为空的目标”选项，由容器维护入口使用；原本机 CLI 默认仍要求目标不存在。非空目录、符号链接、路径越界、损坏备份和非空库继续拒绝，整个过程仍持有维护锁，不覆盖原预算。

维护容器只连接本 Compose 专用数据库、读取业务账户 secret、挂载本项目状态卷与备份卷。业务账户仍是受限 schema owner；不挂 Docker socket，不传模型 Key，不使用集群管理员身份。维护容器的 Python 只运行标准库备份工具，Agent 仍使用应用镜像中锁定的 Python 3.12 环境。

## 预定验收

1. 保留原本机恢复回归；增加空挂载根目录的正例，确认默认模式和非空目标仍拒绝。
2. CI 构建维护镜像，记录客户端、Python 和基础镜像实际版本；配置检查确认无公开端口、非 root、只读根目录和专用状态挂载。
3. 原实例存入已确认外部样例、完成报告、待处理任务、可恢复的脚本模型 checkpoint、明确标注为合成的累计预算；真实个人账本不进入 CI。
4. 运行中备份必须拒绝；停止应用后备份，在独立新 Compose 项目/数据库/状态卷恢复。对比内容 SHA，重新登录读取原报告及导入版本，继续任务和 checkpoint，检查无重复发布/工具执行。
5. 非空目标、损坏或不匹配备份必须拒绝；失败状态不能被当作恢复完成。清理范围仅为该次 CI 创建的测试资源。

预算核账仍是独立门槛：恢复旧备份不能抵销备份后真实发生的费用。本轮无真实模型调用，也不以合成预算证明供应商账单已结算。最终测量、失败和局限在实施后单独记录。
