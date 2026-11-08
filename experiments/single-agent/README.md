# LangGraph 单 Agent 切片

本轮在原 Java/Python 任务协议上增加自然语言问题任务。执行器是 LangChain `create_agent` 生成的 LangGraph 图，模型可选择工具、查看结果、修正参数，再提交结构化决策。确定性模板根据真实工具证据生成金额与比较结果；模型没有 SQL、文件路径、Shell、网络或业务写权限。

## 环境

Python 3.12；直接依赖为 LangChain 1.4.9、LangGraph 1.2.20、SQLite checkpointer 3.1.3、LangChain DeepSeek 1.1.1、DuckDB 1.5.0。完整 49 包及所有下载哈希在根目录 `requirements-agent.lock`。这是独立环境，不覆盖上一阶段的 DuckDB / Wren 环境。

在本项目根目录执行：

```sh
python3.12 -m venv .local/agent-venv
PIP_CONFIG_FILE=/dev/null .local/agent-venv/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple --cache-dir .local/pip-cache \
  --only-binary=:all: --require-hashes -r requirements-agent.lock
python3.12 scripts/java_local.py build
.local/agent-venv/bin/python experiments/single-agent/verify.py .local/single-agent/verification/repeat.json
```

Java 工具链初次安装仍见 [任务闭环 README](../task-loop/README.md)。Java 侧已有的 H2 文件无需新增列：新增请求与结果存在原 JSON 字段，旧指标任务格式兼容；这不是完整生产数据库迁移方案。

离线验收共 **42 项**，其中 18 项继承上阶段任务闭环测试、19 项 Agent/预算/SDK 协议测试、5 项新 HTTP Agent 集成测试。使用真实 LangGraph、DuckDB、JVM 和本地 HTTP；模型响应是脚本或 HTTP Mock，不产生真实模型请求，不能作为准确率证据。

## 新业务请求

```json
{
  "kind": "agent",
  "dataset_id": "demo-ledger-v1",
  "question": "按 UTC，统计 2026-10-05（含）至 2026-10-12（不含）已支付订单，扣除这批订单截至 2026-10-19 00:00（不含）的成功退款后的净额。"
}
```

POST `/api/workspaces/demo-shop/tasks`，仍需 demo 用户 token 与 Idempotency-Key。模型、预算、脚本路径、工作空间选择不能由请求 body 指定。旧 metric/start/end 请求格式不变。

任务 SUCCEEDED 表示执行得到一个完整结果，不等于一定回答了问题。Agent 报告的 `outcome` 分为：

- `answered`：有真实工具证据，报告含指标、查询参数、SQL、金额、输入和实现哈希；可含可比较期间的差值。
- `needs_clarification`：缺时间范围、明确口径或退款截止时间，没有猜测金额。当前补充后创建新任务，尚非多轮人审界面。
- `unsupported`：缺数据或超出能力，例如利润、因果、越权和外部写操作；没有数字结论。

技术异常、预算耗尽等仍走任务 FAILED。`provenance.mode=scripted` 明确表示工程回放；`live` 表示配置了提供方路径。是否真正请求外部模型还应结合评测记录，SDK HTTP Mock 属于工程测试。

## 真实模型评测：仅限个人 Key

模型：DeepSeek 官方 `deepseek-flash`，请求关闭 thinking，temperature=0，每次最多输出 1024 tokens，SDK 自动重试为 0。官方当前版本声明及价格记录在模块研究文档中；别名不是不可变权重。

**由用户自己在本地终端执行**以下命令。Key 使用 getpass 输入，不回显、不写入文件、不进入日志、checkpoint 或报告；只在本次进程与受控子进程内存中使用。不要发送 Key 到聊天，也不要读取/复用电脑已有的提供方凭据。

```sh
.local/agent-venv/bin/python scripts/evaluate_personal_agent.py
```

此入口启动本地 Java 和独立 Worker，先运行 3 道开发题；全部通过才跑 7 道保留题各两轮。输出每题状态，并保存 `.local/single-agent/live-runs/run-*/evaluation.json`，指针文件是 `.local/single-agent/latest-live.json`。只发送仓库合成题、指标说明及聚合结果，不上传公司或私人商家数据。

所有运行共享 `.local/single-agent/personal-budget-2026-11-06.json` 的 **5 元总上限**，不是每次启动各 5 元。每次请求前按官方上下文上界与输出上限预留 2.105344 元；收到 usage 后按高峰缓存未命中价格计算费用上界，释放多余预留。超时或缺 usage 保留整笔预留，所以连续未知请求可能在实际花费很少时提前停止。这个数字是预算占用上界，不是账户实际账单。不要删除账本后继续本轮评测。

定价验证日期固定为 2026-11-06，日期改变后入口拒绝继续付费请求，需重新核实官方模型/价格与用户预算。不得为跑通而把预算限制去掉。未完成真实模型评测时，结果必须标记 pending，不把工程测试写成真实模型正确率。

## 状态恢复与限制

每个逻辑任务在 `.local/single-agent/runs/<task_id>/` 保存 identity、LangGraph SQLite checkpoint、执行次数 journal、派生 DuckDB、报告和简化 trace。identity 绑定输入、问题、提示词、实现、依赖及运行模式；变化后拒绝复用旧状态，避免混用结果。Key 不属于状态。

本地跨进程互斥锁保证同一逻辑任务只有一个 checkpoint 写入者；Java 新租约可在旧进程退出后继续已有图状态。SQLite 使用 sync durability；已保存工具结果可直接用于后续模型步骤。失效租约仍不能发布。旧进程未退出时，新进程得到 checkpoint_busy 并走有限重试；不是跨主机协调或强制抢占。

模型最多尝试 5 次、业务工具最多 4 次。原生 middleware 限制与落盘 journal 同时使用，恢复不重置预算。已发出的付费请求可能在 checkpoint 提交前崩溃，因此重放仍可能再次收费；费用账本必须独立于图状态提前预留。

报告只支持有限指标和比较模板，不是自由长文经营咨询；真实语义准确率需按题集验证。没有前端、多轮对话、跨机器 checkpointer、公开身份认证或生产部署。
