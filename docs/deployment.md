# 部署与恢复手册

当前状态（2026-11-08）：macOS ARM64 上的真实 PostgreSQL、Flyway、受限业务账户、停写备份和恢复，以及实际 Caddy TLS 登录链路已验收。容器、Linux 另行记录；未部署公网。当前免费公网选择及限制见 [部署研究](research/016-deployment-and-database.md)。

## 容器候选与干净验收

`Dockerfile` / `deployment/compose.yaml` / `.github/workflows/verify.yml` 已准备，**尚未构建启动验证，不是已完成的发布版本**。本机匿名 registry 请求超时，没有连接或检查公司的 Docker daemon。下一步在核对后的个人公开 GitHub 仓库的 hosted Linux runner 构建：先解析官方精确版本的镜像 digest，再构建应用/验收镜像，运行离线任务、导入、上下文和模型错误检查；随后在新卷启动 PostgreSQL + 应用 + 实际 Caddy TLS，验证登录、计算、内部 API 隔离和重启持久化。无模型 secret，默认无推送镜像权限。

CI 只对个人 owner `hhhhao139-ai` 运行，checkout/upload action 固定 commit；结果在 `clean-linux-<commit>` artifact 留存 14 天，正式发布前另保存关键结果与链接。还没有真实运行链接或 digest，不提前填写。人工想在自己的 Docker 环境尝试时，必须明确选用自己的 context，不能使用公司的默认环境：

```sh
python3 deployment/prepare_secrets.py
# 将下面 personal 换成自己核验过的个人 Docker context。
# .local/deployment.env 只写非秘密的 COMMERCE_ORIGIN / DOMAIN / ACME_EMAIL。
docker --context personal compose --env-file .local/deployment.env \
  -f deployment/compose.yaml build app
docker --context personal compose --env-file .local/deployment.env \
  -f deployment/compose.yaml run --rm --no-deps app owner
docker --context personal compose --env-file .local/deployment.env \
  -f deployment/compose.yaml --profile public up -d
```

`COMMERCE_ORIGIN=https://自己的域名`，`COMMERCE_DOMAIN=自己的域名`，`COMMERCE_ACME_EMAIL=自己的邮箱`；需要 DNS/80/443/证书状态目录。用户目前没有该资源，因此本次不执行公网命令。应用/数据库不发布宿主端口，只有 proxy profile 对外监听；应用只读根文件系统、UID 1000，状态在独立卷；PostgreSQL 18 卷挂 `/var/lib/postgresql`。不要执行 `down -v` 清理真实运行卷。CI 中只删除该次创建的合成测试卷。

数据库 secret 文件由脚本随机生成，原目录存在时拒绝覆盖；宿主目录 0700，文件只挂载给指定容器。不是模型 Key 存储入口。owner 密码交互设置，只保存哈希。当前容器入口只提供离线 `serve` 和 `owner`，没有偷偷继承机器模型环境变量或重置累计预算的路径。付费交互仍使用原个人终端入口并遵守原预算；后续容器实时模式须连同预算迁移单独验收。

## 本地与部署的区别

本地 H2 Demo 保留原 README 的启动方式。PostgreSQL 部署是单实例、单店主应用，Java/Python 同一机器或应用容器，另有专用 PostgreSQL。不要对已有公司数据库、Docker context、仓库或凭据执行本手册。全部命令均在本个人仓库内运行。

PostgreSQL 账户由部署管理员初始化：建立专用库和 `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS` 登录角色，再用 `deployment/database-privileges.sql` 指定 `app_role` / `app_database` 完成授权。应用仅拿该角色，不拿管理员。schema owner 可以执行 Flyway DDL，此配置不是只读或纯 DML 权限。

使用 `scripts/prepare_owner.py --runtime .local/owner-demo/offline` 在个人交互终端设置本应用密码。PostgreSQL 启动需显式设置 `COMMERCE_DB_URL`、`COMMERCE_DB_USER`、`COMMERCE_DB_PASSWORD`；密码不要放命令参数或 Git 文件。`COMMERCE_DB_URL` 是 `jdbc:postgresql://主机:端口/专用库`。

```sh
.local/agent-venv/bin/python scripts/web_demo.py --owner --postgres \
  --runtime .local/owner-demo/offline --port 18080
```

这仍是回环 HTTP 入口。外部访问必须设置唯一 HTTPS `--origin` 并通过 TLS 代理；不得直接把本地无身份 Demo 开到公网。默认不启用模型，任何模型 Key 仍仅能由用户在个人终端隐藏输入，原 5 元预算不会因换部署重置。

H2 历史不会因为更换 JDBC URL 自动迁移。当前 PostgreSQL 支持新部署及自身版本化迁移；旧 H2 Demo 保留并可导出已有 JSON 报告。没有实现通用 H2→PostgreSQL 任务历史转换器，不能把一份 H2 数据文件直接交给 PostgreSQL。

## 停写备份

先停止本应用启动器并确认它已退出，禁止同时运行另一份 Agent 评测。工具会获取状态维护锁并拒绝有其他数据库连接的备份。使用项目专用数据库连接参数；`COMMERCE_DB_PASSWORD` 仅传给显式目标，不读取机器 `.pgpass` 或默认配置。

```sh
.local/agent-venv/bin/python scripts/backup_state.py backup \
  --host 127.0.0.1 --port 5432 --database commerce --user commerce_app \
  --runtime .local/owner-demo/offline --agent .local/single-agent \
  --bundle .local/backups/first
```

上面的主机/端口/库必须换成自己初始化的专用实例。默认使用项目内 PostgreSQL 客户端；部署镜像可显式提供 `--pg-bin`。目标目录必须不存在。保存完整目录及打印的 manifest SHA，不能只保存 dump。备份包含数据、登录哈希和内部 token，应私有保存；不上传到 GitHub。清单缺失表示备份未完成。

所有状态路径须直接位于本项目 `.local` 内，不接受含 `..` 的路径或符号链接（包括 manifest）。路径反例与真实恢复回归见 [阶段 5e](baselines/phase-05e-2026-11-08.md)。

## 恢复到空目标

恢复前使用相同代码、jar、依赖锁与合成基准文件，初始化独立空数据库及上述 schema 权限，停止所有应用写入。只恢复自己生成的可信备份：SQL dump 可执行数据库操作，SHA 仅检测损坏。

```sh
.local/agent-venv/bin/python scripts/backup_state.py restore \
  --host 127.0.0.1 --port 5432 --database commerce_restore --user commerce_app \
  --runtime .local/owner-demo/restored --agent .local/single-agent-restored \
  --bundle .local/backups/first
```

示例使用隔离目标用于演练。真正切换需在停写状态下把确认恢复的 Agent 状态放到该实例唯一 `.local/single-agent` 位置；绝不直接覆盖已有账本，亦不能同时启动旧实例。工具本身拒绝已有目录和非空数据库。失败后保留 `RESTORE_INCOMPLETE`，不要手动删标志强行启动；查明原因后在新的空库/空目录重做。

演练须核对 owner 登录、历史报告和数据 source/mapping SHA、未完成任务的恢复与唯一发布、checkpoint 不重复工具、累计预算字节及账单连续性。恢复旧备份不能回退备份后发生的费用；付费能力在核账完成前保持关闭。此方案有停机窗口，不是连续归档或零数据丢失承诺。

复现本阶段自动演练：

```sh
.local/agent-venv/bin/python scripts/bootstrap_postgres.py
.local/agent-venv/bin/python experiments/postgres/verify_backup.py .local/backup-check.json
```

源码安装与测试只创建并停止自己生成的项目内临时 PostgreSQL，不发现、复用或操作机器已有数据库。已测结果见 [阶段 5b](baselines/phase-05b-2026-11-08.md)。
