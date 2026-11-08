# 本地 Java / Python 任务闭环

范围：一个 Java 业务服务，一个或多个 Python Worker，单文件 H2。执行已有确定性 DuckDB 指标，不调用模型，不是已经完成的 LangGraph 应用。

## 环境与运行

当前验证平台是 macOS ARM64、Python 3.12.16、Temurin JDK 21.0.13+9、Maven 3.9.16、Spring Boot 4.1.2、DuckDB 1.5.0。JDK/Maven 引导脚本目前仅覆盖此平台；Linux 容器部署单独验收。

以下命令均在个人项目根目录执行。不要使用公司 Maven 配置、仓库凭据或 SSH。Java 工具链引导下载约 200 MB JDK，SHA 校验通过才解压；Maven 启动时明确覆盖 user/global settings、禁用启动 rc，依赖和构建文件均留在本目录。

```sh
# Python 3.12+；本机可使用已有 .local/controlled-venv/bin/python
python3.12 scripts/bootstrap_java.py
python3.12 scripts/java_local.py build

# 首次需要建立仅含 DuckDB 的项目环境，已有则跳过
python3.12 -m venv .local/controlled-venv
PIP_CONFIG_FILE=/dev/null .local/controlled-venv/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple --cache-dir .local/pip-cache \
  --only-binary=:all: -r requirements-ledger.txt

# 一条命令启动真实 Java 和独立 Python Worker，输出结果后关闭本次启动的服务
.local/controlled-venv/bin/python scripts/task_demo.py
```

预期结果：SUCCEEDED、23000 分（230 CNY），完整 SQL、参数、数据／实现哈希和状态事件保存在 `.local/task-loop/demo/latest-result.json`。任务与结果保存在 `.local/task-loop/demo/tasks.mv.db`。再次运行会创建新演示任务，历史数据保留。

手动常驻运行：终端一执行 `python3.12 scripts/java_local.py serve`；终端二执行 `.local/controlled-venv/bin/python -m commerce_worker.worker`。默认端口 18080，固定监听 127.0.0.1。不要把此配置作为公开服务部署。

## 协议 task-v1

| 调用者 | 接口 | 语义 |
| --- | --- | --- |
| demo 用户 | POST `/api/workspaces/demo-shop/tasks` | Idempotency-Key 请求头；返回 202 和持久化任务。同 key 不同请求返回 409 |
| demo 用户 | GET `/api/workspaces/demo-shop/tasks/{id}` | 状态、尝试次数、请求、结果与审计事件；不返回租约 token 或数据原文 |
| demo 用户 | POST `/api/workspaces/demo-shop/tasks/{id}/cancel` | 终止发布权；已成功或失败返回 409；重复取消幂等 |
| Worker | POST `/internal/tasks/claim` | 空闲才领取；无可执行任务返回 204；成功返回任务、输入 bytes 的 base64、哈希、租约 token 和 lease_ms |
| Worker | POST `/internal/tasks/{id}/heartbeat` | `lease_token`，过期或失效返回 409 |
| Worker | POST `/internal/tasks/{id}/complete` | `lease_token`、`result`；同成功租约同 JSON 对象重传幂等，篡改或旧租约 409 |
| Worker | POST `/internal/tasks/{id}/fail` | `lease_token`、布尔 `retryable`、枚举风格 `error_code`；短暂错误退避后重试，最大 3 次 |

创建 body 示例：

```json
{"dataset_id":"demo-ledger-v1","metric":"cohort_net","start":"2026-10-05T00:00:00Z","end":"2026-10-12T00:00:00Z","as_of":"2026-10-19T00:00:00Z"}
```

金额指标原样复用上一阶段 Python 工具定义。Java 注册 `demo-ledger-v1`，每条新任务冻结快照 bytes；调用者不能传文件路径、任意 SQL 或远程 URL。HTTP JSON 请求上限 64 KiB，demo 注册快照上限 1 MB；这不是正式文件上传接口。

两个角色分别读取 `.local/task-loop/demo/user.token`、`worker.token`，程序生成随机值，权限 0600。请求带 `Authorization: Bearer …`，不使用机器已有凭据、不打印 token。仅支持 demo-shop，不是生产用户管理或多租户认证。Worker 只连接显式 127.0.0.1 地址，关闭代理和重定向，避免把本地 token 发给别处。

默认租约 15 秒，Worker 约每秒续租；网络失败保守停止本地计算，任务等待重新领取。过期任务由后续 claim 回收，若没有 Worker 轮询，不会自动变为 FAILED。退避为 1 秒 × 当前尝试次数，默认单次执行上限 60 秒，CLI 可设到 300 秒。暂无整体任务 SLA/模型费用预算。

H2 文件只允许一个 Java 进程拥有；并发 HTTP 使用事务行锁，领取后在锁内重新判断资格。此模式不能用来证明多 JVM 或 PostgreSQL 的并发语义。终态和结果、审计事件同事务落库；heartbeat 不逐次记审计事件，避免高频膨胀。

## 验证

```sh
python3.12 experiments/task-loop/baseline.py
.local/controlled-venv/bin/python experiments/task-loop/verify.py .local/task-loop/verification/repeat.json
```

验收会启动真实 JVM、使用真实 H2 文件与 HTTP、运行 Python/DuckDB，并注入丢响应、失效租约、并发抢占、超时、取消和 SIGKILL 重启。每次使用独立的 `.local/task-loop/verification/run-*`，日志与私有 token 不提交。测试结束关闭它启动的服务，不清理其他进程。报告中记录每个用例和耗时，不把模拟故障说成实际生产事故。

Java Maven 构建目前没有单独的 JUnit 测试；业务可靠性验收入口是上述跨语言测试，`mvn package` 成功不代表验收通过。原有 46 项 Python 数据测试也须通过；它们需要 `experiments/query-layer/README.md` 的完整实验环境。

## 重要限制

- 全部是本地单实例、合成快照和确定性工具；没有真实模型质量、真实商家试用、负载压测或上线结果。
- 不承诺执行一次。丢失响应和租约过期可能触发重算，但过期尝试不能发布。只有发布权受 Java 控制，未来外部写操作需要各自的幂等/事务协议。
- Worker 是受信服务；Java 验证结果与任务/数据/口径的绑定，不重新计算数学正确性。未来 Agent 输出必须先经过确定性校验。
- 取消在 Java 立即阻止后续发布，Python 在下一次心跳察觉后终止子进程；不是瞬时停止，也不能由此证明强制杀掉整个主机后所有外部操作均已停止。
- 单文件数据库无跨主机高可用。schema.sql 仅支持初始建表，无升级迁移方案；引入正式生产数据库前加入迁移版本与回滚验证。
- 任务表并非通用调度平台；到人审等待、跨服务补偿、多实例或显著积压时，按研究记录重评 Temporal、Agent Server、PostgreSQL 或 MQ。
