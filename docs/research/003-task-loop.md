# Java / Python 任务闭环：实施前决策

日期：2026-11-06。状态：方案已决策，效果以随后实验记录为准。本文先于应用实现与验收编写。

## 问题和参照

当前只有本地 Python 数据工具，没有 Java 服务，所以不能声称修复了已有线上丢任务故障。新增能力是：用户提交一次分析后，即使网页请求结束、Worker 退出，也能查询同一任务的状态、结果和证据。

简单参照是内存任务队列，以及收到请求就执行并追加结果的回调。`experiments/task-loop/baseline.py` 演示进程重启丢失内存任务、确认丢失后重试重复发布；这是刻意构造的反例，不是现有系统测量。改造前完整源码快照位于 `.local/baselines/before-task-loop-20261106/`，SHA-256 为 `ebe9b3a5f8b3bd7e67a63dfb06a5ddd609744899fc4a3d63fb929f864926bd94`。

## 同类实现与可借鉴机制

以下均为 2026-11-06 访问的官方滚动文档，未固定文档 commit；是文档声明，不代表在本机运行过这些产品。

| 来源 | 官方机制 | 本项目借鉴 / 差异 |
| --- | --- | --- |
| [LangSmith Agent Server](https://docs.langchain.com/langsmith/agent-server) | API、持久化 run 与 queue worker；默认 PostgreSQL 保存运行数据，Redis 用于临时通知、取消和流式事件 | 区分业务任务、执行与展示；不把通知渠道当状态真相。我们已有 Java 业务边界，暂不引入第二套 run 服务；复杂图部署时重新评估 |
| [Temporal Task Queues](https://docs.temporal.io/task-queue) | Worker 有空闲能力时拉取，Workflow/Activity 任务持久化 | 采用拉取形成容量边界；不自行实现工作流历史重放、跨服务补偿或通用 DAG 调度 |
| [RabbitMQ Reliability](https://www.rabbitmq.com/docs/reliability)（页面 4.4） | 确认丢失可导致重发，消费者仍需幂等 | 对领取和回传做重复/过期处理；MQ 不是业务结果仅发布一次的充分条件 |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | 图执行状态保存与恢复 | 后续用于 Python 图内部；Java 的取消、租约和发布权限仍需独立执行 |

## 通信和调度比较

| 选择 | 收益 | 代价 / 为什么此时不选 |
| --- | --- | --- |
| 同步 HTTP 或内存后台队列 | 最少组件，容易调试 | 超时语义不明确；内存状态不能跨进程重启恢复 |
| HTTP JSON + 持久化任务表 + 租约拉取（本轮） | 创建任务与入队是同一条业务记录；Java 单一状态权威；Python 不访问业务数据库 | 自己维护有限状态机、心跳与幂等；轮询有延迟和数据库开销；仅覆盖有限的只读任务 |
| RabbitMQ + 业务任务表 + outbox | 成熟投递、消费与消息积压管理 | 业务库写入与发消息需要 outbox/对账；仍需要消费者幂等与结果 fencing；当前无吞吐证据支持增加服务 |
| Temporal | 成熟持久化流程调度，适合长流程和跨服务协作 | 新增运行服务及 Workflow/Activity 模型；本轮一步只读任务无需通用流程能力；未来人审等待、补偿流程出现时重选 |
| LangSmith Agent Server | 现成 Agent run API、队列、图持久化、流式接口 | 需处理 Java 业务任务与 run 的映射及取消一致性；到真实 LangGraph 部署阶段比较维护成本与托管条款 |

HTTP JSON 能被 Java/Python 直接调试、版本化并记录业务错误；当前没有高吞吐序列化或双向流式需求证明 gRPC 更合适。任务状态先 GET 轮询；SSE 是后续展示能力，不作为可靠队列。此处为基于当前范围的工程判断，不声称性能优于 MQ/gRPC。

## 本轮边界与协议

- Java 21 + Spring Boot；本机 Java 8 不修改。JDK、Maven、依赖缓存独立放在 `.local/`，只用公开官方源，不读取机器 Maven 配置。
- 本地演示用 [H2 文件模式](https://h2database.com/html/features.html)，单 Java 实例，事务更新与行锁；不启用 TCP、Console 或 PostgreSQL 兼容模式。[PostgreSQL SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.htm) 是后续多实例队列候选，必须用真实 PostgreSQL 另测，不能沿用 H2 测试结论。
- 状态为 QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED；暂时失败可延迟重新排队，最多 3 次尝试。领取后发租约 token，每次重领更换；过期或取消的尝试不能续租、发布或失败覆盖。
- 创建请求带 Idempotency-Key：同 key 同规范化请求返回同任务；不同请求 409。数据库唯一约束兜底并发。
- 结果与状态、审计事件在同一事务写入；同租约同 JSON 结果重传幂等成功，改变结果或旧租约返回 409。只能保证一个有效发布，不能保证代码只执行一次。
- Python 单进程一次领取一个任务，子进程执行现有只读指标工具；心跳丢失/取消后终止子进程；本轮不是 LLM、单 Agent 或多 Agent 效果测试。
- Java 固定注册样例 dataset，输入 bytes 与 SHA-256 随任务冻结；不接受客户端路径或远程 URL。保留 SQL、参数、输入和实现哈希作为证据。
- 仅监听 127.0.0.1；本地 demo 用户与 Worker 使用分开的随机 token，文件位于被忽略的 `.local/`；仅 demo-shop 工作空间。此机制不是生产多租户认证。
- 成熟组件处理 HTTP、JSON、连接池与数据库事务；自写范围限制为业务状态机。无分布式 DAG、无定时 cron、无业务写操作。

## 预先确定的验收

所有用例必须通过，不设吞吐改进百分比：真实 Java HTTP → Python → DuckDB → Java 结果为 23000 分；重复提交与冲突；并发领取；租约失效重领和旧回传拒绝；心跳续租；重复完成不追加发布；取消后不复活；暂时失败退避与重试上限；不支持的指标条件终止；执行超时；Java 进程重启后的排队、运行、完成状态恢复；角色/空间隔离；输入和结果证据绑定。先保存失败输出，再修复复测。

局限预先声明：单 Java 文件数据库没有高可用；进程崩溃不等于掉电/磁盘损坏测试；没有真实模型、外部试用、公开部署或高并发负载结论。预算、LangGraph checkpoints、真实 PostgreSQL、生产身份认证另有验收。
