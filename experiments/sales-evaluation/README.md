# 商品贡献专项评测

本目录测试 **Agent 是否正确选择查询和引用结果**。离线工程测试与真实模型验收是两个不同步骤。

## 已完成：评测器工程检查

```sh
.local/agent-venv/bin/python experiments/sales-evaluation/verify.py .local/sales-evaluation/check.json
```

11 个 unittest 测试方法，覆盖 9 道新题及 10 道原题的期望值、实际 LangGraph/工具生成的脚本报告、独立建库重算，以及金额、占比、其他贡献、类型、引用、口径、日期等篡改反例。ScriptedModel 的业务期望可以通过，但 `grade()` 必须返回 `not_a_real_model_run`，不会计入真实模型成功率。

预期值来自固定合成账本的手工核算。重算使用现有工具验证可复现性，不能单独证明工具算法正确；算法另由手工值和阶段 2e 的独立随机账本对照验证。

## 个人真实模型验收

2026-11-06 首轮已完成：开发 3/3，保留两轮各 6/6，15 份报告重新审计通过。实际费用、题目边界与后续限制见 [阶段 2h](../../docs/baselines/phase-02h-2026-11-06.md)。下面命令用于复现；再次运行会产生新的模型费用。

在你自己的交互终端运行，不要将 Key 放到命令参数、文件或聊天：

```sh
.local/agent-venv/bin/python scripts/evaluate_personal_agent.py --suite sales-change
```

运行目录可以任意，绝对命令见应用会话；脚本使用自身路径定位个人项目。只使用 `examples/ledger/snapshot.json` 合成账本。先 3 道开发题，通过后 6 道保留题各跑 2 轮，共计划 15 次任务；每个任务可能多次模型请求。任一轮保留集至少 80%，发布金额/引用错误必须为 0。原 5 元累计预算账本继续生效，不会因换题集清零；未知费用保留全额预留。

模型、Prompt、工具和调用限制沿用阶段 2e。2026-11-06 重新核对 [DeepSeek 官方人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：Flash 高峰缓存未命中输入 2 元/百万 token、输出 8 元/百万 token，与现有保守账本一致。跨日期运行会被现有价格检查拒绝，需重新核实后再处理，不能直接重置账本。当前没有 Search/Fetch 工具调用。

结果位于 `.local/single-agent/live-runs/run-*/evaluation.json`；本专项指针为 `.local/single-agent/latest-sales-change-live.json`，原 `.local/single-agent/latest-live.json` 保留。指针只是本地文件位置，不是已验收标志。报告的 `completed`、`heldout_passed`、逐题错误和发布审计都要检查。

开发集：完整贡献、口径未明、时间未明。保留集：Top N 与其他贡献、无交易、零基数、明确时区转换、缺数量/单价、净额缺商品退款归属。按问题族区分，部分题共用账本与期间；尚不代表独立商家、私有输入或真实用户的泛化能力。运行后不能靠降低门槛或改写保留题制造通过结果。

未加 `--suite` 时仍运行原题集；新专项没有改写旧实验文件。
