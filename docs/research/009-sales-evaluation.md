# 商品贡献的评测器与真实模型验收准备

日期：2026-11-06。状态：评测器工程检查见 [阶段 2g](../baselines/phase-02g-2026-11-06.md)；随后真实模型初验收结果见 [阶段 2h](../baselines/phase-02h-2026-11-06.md)。以下保留实现前的比较与门槛。本项在前端改造完成后接续。

## 已复现的问题

旧 `evaluate_personal_agent.py` 按 `evidence.result.value` 读取标量，并把所有证据交给 `ledger.query` 重算。商品贡献返回 `totals / increases / decreases`，需要调用 `analyze`。实际运行 LangGraph＋ScriptedModel＋DuckDB 得到正确 -13000 分，旧评分器抛 `KeyError: value`，重算返回 `published_report_audit_failed`。这是评测器不支持新类型，不是模型推理失败；[原始复现](../probes/sales-evaluation-before-2026-11-06.json) 保留。

修改前源码：`.local/baselines/before-sales-evaluation-20261106/`，184 文件，SHA-256 `38ccd4cb1d43742decad4f51ead9233fcd7a3dbe1ac65db8bdb4d1e19f77e9f7`。

## 官方调研与备选

2026-11-06 访问，滚动文档未固定版本：

| 来源 | 可借鉴机制 | 本项目边界 |
| --- | --- | --- |
| [LangSmith evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts) | 区分样本、运行、评分器；代码评分适合结构、分类等确定性条件，LLM 裁判需复核 | 固定题目、预期查询和手工金额；错误分为任务效果、数字/引用发布、运行失败；不上传本地轨迹到云平台 |
| [Ragas agent/tool metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/) | 工具名和参数准确性与最终目标达成是不同维度 | 核对被最终引用的查询参数和结果，不强制 inspect 调用顺序；不能宣称本方案已评分全部中间工具轨迹 |

选项：只跳过缺 value 的证据会漏判；LLM 裁判或引入完整评测平台增加成本且不能替代精确金额校验；**扩展现有代码评分与独立重算**可复用预算、Java/Worker 和产物记录，因此采用。只为结构化商品贡献添加一种期望值，不修改 Agent Prompt、模型、工具或预算。

## 验收定义与局限

- 预期查询匹配期间、指标、Top N，返回字段匹配总额、方向合计、各商品、其他贡献、变化率、勾稽。时间规范为 UTC。
- 发布审计重新建库并执行同一受控工具，验证整个证据、引用 ID 和程序生成的完整报告。它验证可重算性；独立手工期望值及已有随机账本对照测试另防止同一实现自证。
- 缺字段、额外/错误证据必须判失败，不能抛异常中断全部实验。ScriptedModel 通过业务期望不等于真实模型通过，真实入口继续要求 provenance.mode=live。
- 开发集 3 题（完整分解、口径未明、期间未明）；保留集 6 题（Top N 与其他、无交易、零基数、时区转换、量价数据缺失、退款口径与商品归属限制），两轮。开发全过后才运行保留集，每轮至少 80%，所有发布金额或引用错误容许数为 0。
- 按问题族拆分，不只改写句子；部分题共用同一合成账本和日期，不能据此声称跨商家或跨数据集泛化。题集在新模型调用前固定，不因结果修改门槛。
- 本轮只有离线评分器与真实组件验证，无模型费用。后续个人终端选 `--suite sales-change` 输入 Key，共用既有 5 元累计账本；不得重置预算。评测题不提供预期答案给运行中的 Agent。
