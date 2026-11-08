# 上下文和固定双专家对照

**2026-11-08 更新：** 首次真实合并验收首题中断，两次模型尝试未收到 usage；三候选对照尚未开始。原共享账本含两项未知预占，目前保守占用 4.525418 元，不能视为实际消费或直接清零。入口现会在旧未知用量存在时停止新付费验收。见 [阶段 4b](../../docs/baselines/phase-04b-2026-11-08.md)。下文命令是复现入口，不表示当前预算可以直接继续。

方案及预设采用门槛见 [研究 014](../../docs/research/014-context-and-specialist-evaluation.md)。产品默认仍是完整上下文单 Agent；`commerce_agent.experimental` 是研究候选，没有公共 API 参数。

离线验收（不会请求模型）：

```sh
.local/agent-venv/bin/python experiments/context-evaluation/verify.py
```

12 组测试包含 35 个原指标/销售/退款/复合问题的脚本决策，真实 LangGraph、DuckDB、checkpoint 恢复、证据篡改、SDK 请求序列化、专家能力与次数限制、共享预算延续，以及整个 42 次候选运行的模拟 HTTP 调度。测试通过不能解释为语言理解效果或模型省费。最后一项模拟接口返回固定 token 数，门槛正确拒绝宣称摘要节省 token 或双专家提高质量。

由本人在个人终端执行一次隐藏 Key 输入的合并验收：

```sh
.local/agent-venv/bin/python scripts/evaluate_release.py
```

先通过真实 Java/Worker 运行当前版原指标、销售变化和退款变化题集；然后三个候选使用相同直接 Python 路径进行模型流程比较，避免把 HTTP 调度开销误归因于候选。2 个开发题、6 个保留题各两轮，按题轮换候选顺序；开发未通过的候选不打开保留集。摘要候选达到预设门槛后，继续跑三套原题并与本次完整模式逐题比较。最后只输出研究结论，不自动切换产品默认。

`--only-comparison` 只适合显式重跑对照，不能代替发布前回归。每次运行都是新增付费调用，共享同一原账本 `.local/single-agent/personal-budget-2026-11-06.json`，不得删除或改名。65 个历史请求和 0.314730 元占用保留；未知 usage 保留全额预占，严格预占可能让评测在实际花满 5 元之前停止。中途未完成时不宣称候选通过。不要重复点击运行来绕过预算或挑选结果。

价格只在已人工复核日期允许实时调用。本阶段复核 2026-11-08；以后遇到 `model_pricing_needs_revalidation`，先核对官方模型、上下文和峰值费率再维护策略，不能新建预算。Key 不写文件，不继承机器已有模型/代理/追踪凭据。

结果指针为 `.local/context-evaluation/latest-live.json`。其中 `release.json` 链接三套回归原报告、`comparison.json` 与可能执行的 `compact-regressions.json`；完整报告、usage、耗时、失败、响应模型名、源码/题集 SHA 和累计费用保留在同一运行目录。即使出口码为 0，也要看各候选门槛；跑完不等于多 Agent 值得采用。
