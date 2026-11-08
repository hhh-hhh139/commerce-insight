# PostgreSQL 业务库验收

先读 [部署选型](../../docs/research/016-deployment-and-database.md)。本目录验证真实 PostgreSQL，不使用 H2 的 PostgreSQL 兼容模式。

当前 macOS ARM64 开发环境的项目内准备：

```sh
.local/agent-venv/bin/python scripts/bootstrap_postgres.py
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python experiments/postgres/verify.py .local/postgres-verification.json
```

工具链从 PostgreSQL 官方下载 18.6 源码，验证固定 SHA-256，安装到 `.local/postgres-toolchain/installed`，不注册系统服务。支持下载续传，完整 SHA 验证前不解压。该工具链为本机测试省去 ICU/TLS/压缩等可选项，不能代替部署镜像的验收。

验收脚本创建全新 `.local/postgres-verification/cluster-*`，随机回环端口、关闭 Unix socket、随机临时测试密码、SCRAM 认证；客户端禁用个人 psql 配置，测试后停止自己的集群。测试用户拥有该临时集群的管理权限，不能据此宣称部署账户已完成最小权限设计。不发现或连接机器上已有数据库。

默认运行任务/导入/网页原行为契约、Flyway 重复启动、H2 SQL 不兼容反例、迁移校验与失败升级回滚。单独复现三项迁移边界：

```sh
.local/agent-venv/bin/python experiments/postgres/verify.py .local/postgres-migration-check.json --migrations-only
```

`postgres` Spring profile 要求显式 `COMMERCE_DB_URL`、`COMMERCE_DB_USER`、`COMMERCE_DB_PASSWORD`。缺项不会悄悄降级为 H2。它关闭 `spring.sql.init`、启用 Flyway；默认本地 H2 反之。部署启动器与容器配置后续完成，当前不要把这些环境变量指向非个人授权数据库。

初始 PostgreSQL schema 在 `services/business-api/src/main/resources/db/migration/postgresql/V1__business_tasks_and_datasets.sql`。部署后不得修改已执行迁移；用新增版本演进。禁止自动 clean、禁止在非空未知库自动 baseline。H2 历史数据不会自动迁入 PostgreSQL；这是独立的数据迁移与备份恢复事项，不能只改 JDBC URL 就认为完成。

实际结果见 [阶段 5a](../../docs/baselines/phase-05a-2026-11-08.md)。容器、备份、TLS、最小权限部署用户、干净 Linux 和个人公网演示仍待单独验收。
