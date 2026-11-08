# 单 Agent 接入：实施前研究与验收

日期：2026-11-06。实施前方案记录。真实模型使用用户授权的个人 DeepSeek API，总测试预算不超过 5 元；不读取本机已有模型凭据，不把 Key 写进文件、日志或聊天。

## 当前问题与基线

现有任务 API 要求调用者明确提供 metric/start/end/as_of，不能直接处理自然语言问题。这是功能缺口，不是已经发生过的模型故障。保留原确定性入口作为计算真值基线；新增能力要把“模型理解和工具选择”与“计算准确性”分别验收。

改造前源码 79 个文件，完整快照 `.local/baselines/before-single-agent-20261106/source.tar.gz`；SHA-256 `9a267162889cd6c09842b7af34cf4fc314493f83eb0ad4eab545f5508d030a51`。

## 官方调研（2026-11-06 访问）

| 官方资料 | 可借鉴机制与范围 |
| --- | --- |
| [LangChain overview](https://docs.langchain.com/oss/python/langchain/overview)、[Agents](https://docs.langchain.com/oss/python/langchain/agents) | create_agent 提供模型与工具循环，运行于 LangGraph；可配置模型、工具、系统提示和 middleware。采用现成循环，不自行实现消息协议 |
| [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents) | 区分固定步骤工作流与模型动态选工具。我们的真实 Agent 必须由模型选择查询参数，而不是正则匹配问题后伪装模型理解 |
| [Dify Agent](https://docs.dify.ai/en/cloud/use-dify/nodes/agent) | 原生 function calling、受控工具与最大迭代数。借鉴工具描述、参数验证、执行上限；没有部署 Dify，不声称比其更快 |
| [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) | 包含规划、文件系统上下文与子 Agent 的更完整执行框架。当前有限指标问答没有证据需要文件编辑/子 Agent，暂不引入 |
| [Anthropic Building effective agents](https://www.anthropic.com/research/building-effective-agents) | 2024-12-19 发布，页面提示工具生态已变化；仅借鉴从简单方案开始、理解框架内部与控制成本的原则，不用旧文章替代当前 API 文档 |
| [Structured output](https://docs.langchain.com/oss/python/langchain/structured-output)、[middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in) | ToolStrategy、模型/工具调用数限制。结构正确不等于结论真实，最终还要验证证据引用 |
| [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance) | SQLite 适合本地实验；sync durability 在进入下一步前保存状态。重放仍可能重新执行外部请求，不能代替业务幂等和费用预留 |

以上为官方滚动文档，未固定 commit；源码行为将以安装的固定版本和实际测试补充验证。

## 最小方案比较与选择

| 方案 | 收益 | 本次取舍 |
| --- | --- | --- |
| 保持明确参数的确定性 API | 便于验证、无需模型费用 | 保留为兼容入口与计算真值；不解决自然语言入口 |
| 一次模型结构化提取 → 固定查询 | 最简单，适合已知单指标 | 作为简单问题的参照；不能充分覆盖需要观察工具结果再决定是否继续/澄清的任务 |
| 原生 SDK 手写工具循环 | 依赖较少，控制直接 | 需要重复维护工具消息、结构化输出、次数限制、恢复等。没有测得质量优势，当前不重写 |
| create_agent（本轮） | 复用 LangGraph 运行图、工具循环、结构化输出与 middleware | 保留强单 Agent 基线；自写业务工具、证据验证和 Java 协议 |
| 手工 StateGraph | 分支和节点完全显式 | 本轮通用模型—工具循环没有特殊分支需求；复杂确定性校验/人审出现后可组合外层图 |
| Deep Agents / 多 Agent | 更多上下文和委派能力 | 当前缺少支持额外复杂度的效果证据，不进入默认执行路径 |

不是为了简历使用框架：现成组件解决循环和状态管理，我们负责解释业务边界及验证效果。标准循环只是初始选择，不声称 create_agent 自动提高问答准确率。

## 本轮范围

- Java 接受新增 kind=agent 的问题任务，继续管理身份、任务、租约与最终发布；保留旧指标请求。模型不能选择工作空间、原始文件路径、任意 SQL、网络或 Shell 工具。
- Python 暴露数据能力说明和命名指标工具。Agent 自主选择指标、参数及证据；返回结构化决策，最终金额、差值和展示文案由已校验工具结果与模板生成，避免自由文本夹带无证据数字或因果结论。
- 先覆盖单指标、同口径两期比较、订单批次净额、成功退款和数据不足澄清；相对日期缺少参照、口径含糊、利润/因果问题不得猜测。
- clarification 是本次分析的明确结果；先让调用者补充后创建新任务，不声称已完成多轮人工 interrupt 产品流程。
- 本地 SQLite 保存图状态；必须绑定 task、冻结数据、问题和运行配置。Java 发布租约与图 checkpoint 是不同层次，不共享给其他任务。跨机器恢复与 PostgreSQL checkpointer 不在本轮结论中。
- 默认最多 5 次模型调用、4 次业务工具调用；单次模型输出最多 1024 tokens；Worker 总时限仍由代码控制。重试、恢复不能绕过费用上限。
- 首个真实模型为官方当前 `deepseek-flash`，明确关闭 thinking；模型别名可能更新，记录请求名、响应模型标识和提供方版本声明，不能承诺权重固定。

## 个人 API 与预算

[DeepSeek 中文价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) 当日声明 Flash 高峰缓存未命中输入 2 元/百万 tokens、输出 8 元/百万 tokens，上下文 1M；当日版本声明 DeepSeek-V4.1-Flash-1017。[工具调用说明](https://api-docs.deepseek.com/guides/tool_calls/) 支持非思考模式 function calling，明确工具由应用执行。

程序使用公开官方端点，不读取默认 DEEPSEEK_API_KEY 或公司配置。真实验收入口让用户在本地终端通过 getpass 输入个人 Key，仅传到本次进程及受控子进程内存，不保存 Key。模型调用前持久化保守费用预留，成功后按实际 usage 与高峰未命中价格结算上界；响应未知/缺 usage 时保留预留，不当作免费。SDK 自动重试关闭，所有真实评测共用 5 元账本，日期/价格变化后需重新核实。此上界不是账单实际扣款。

## 预先定义验收

工程测试必须全部通过：旧指标闭环兼容；真实 LangGraph 工具循环（脚本化模型明确标识）；多工具/同口径比较；错误参数、未知工具、伪造金额/引用、不同单位比较被拒；数据不足返回限制；调用上限；跨任务 checkpoint 隔离；中断恢复和预算不重置；Java agent 结果绑定；取消与旧租约不发布；Key 不进入 checkpoint/报告。

真实模型题集先固定开发集和按问题族隔离的保留集，不能从脚本化响应导出模型成功率。初验收目标：所有已发布数值和引用可重算、越权/伪造证据零接受；保留集任务成功率至少 80%，逐题公开失败原因，重复两轮记录波动。在看到保留结果后不得同轮改题/改阈值并声称通过。保留集失败后若调参，应换新的独立保留集。

只有真实模型完成后才判断自然语言能力是否通过；未配置个人 Key 时，工程接入与真实模型验收分别记录，不把离线测试写成 Agent 准确率。
