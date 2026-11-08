# 销售变化的商品贡献分析

分析口径：**订单支付时间落在区间内的已支付金额，不扣退款；商品金额来自已经确认分摊的明细 line_minor**。比较两个等长、有先后、无重叠的半开区间。不是含该商品的整单金额，不是平台通用净销售额，也不做量价或因果推断。

## 网页运行

使用已有项目内环境，在个人项目根目录执行：

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python scripts/web_demo.py
```

打开 `http://127.0.0.1:18080/demo/index.html`：

1. 选择内置样例或已确认的导入数据版本。旧导入版本可直接用，无需重新映射；基于已有 paid_amount 能力及账本明细契约开放。
2. 指标选择“销售变化来自哪些商品 · 已支付金额，不扣退款”。上方开始/结束为基期，下方为对比期；网页时间使用 UTC 00:00。
3. 样例基期 `2026-10-05` 至 `2026-10-12`，对比期 `2026-10-12` 至 `2026-10-19`，均不含结束日期。默认每个方向展示前 5 个商品。
4. 运行后核对总额、下降/增长榜单和其他贡献，展开勾稽与 SQL 证据，下载 JSON。任务记录、URL、刷新和重启均能恢复历史结果。表单是新查询入口，不会自动恢复历史任务的全部参数；报告中保留了实际执行期间和请求。

预期结果：

| 商品 | 基期 | 对比期 | 变化 | 分方向占比 |
| --- | ---: | ---: | ---: | --- |
| P3 | 100.00 | 0.00 | -100.00 | 全部下降金额的 71.43% |
| P2 | 40.00 | 0.00 | -40.00 | 全部下降金额的 28.57% |
| P1 | 140.00 | 150.00 | +10.00 | 全部增长金额的 100.00% |

单位 CNY。基期 280 → 对比期 150，总变化 -130（-46.43%）；下降合计 -140，增长抵消 +10。占比不是占净变化，按方向分别计算，舍入后的显示值可能不恰好加到 100%。期间有额/无额不能推出新上架或下架。

默认运行不需要模型 Key。结束用 Ctrl+C，本地数据保留在 `.local/web-demo/offline/`。这是无身份认证的本机演示，不可直接暴露公网。

## Agent 与工具

新增 `query_sales_change(start,end,current_start,current_end,top_n=5)`，与表单 Worker 共用 `commerce_data.sales_change.analyze`。一次业务工具调用内部有一条商品聚合 SQL、两条独立订单总额 SQL。所有金额、排序、占比、剩余贡献和文字摘要由代码生成；模型只选工具、参数与成功 evidence_id，不让模型填写金额或经营原因。

工具已完成真实 LangGraph + 脚本模型接入验证，**脚本不理解语言**。新 Prompt/工具的真实模型选择质量还没有评测。待在个人终端显式启用实时模式、并允许该导入版本用于模型后，可使用明确问题：

> 按 UTC，比较 2026-10-05 至 10-12 和 10-12 至 10-19 的已支付金额，均含开始、不含结束，不扣退款。金额变化主要来自哪些商品？

实时模式仍使用既有个人共享预算、价格日期门槛及 Key 隐藏输入；不新增 5 元额度。模糊“销售额”“上周”需要确认口径/日期；只有退款或利润诉求时不能擅自改成支付金额。真实评测应另设独立问题集，报告工具选择、参数、澄清/拒答与证据正确率，不能用本次脚本测试声称自然语言效果提升。

## 接口与确定性 CLI

原任务创建接口接受（省略 top_n 时为 5；API 范围 1–10）：

```json
{
  "dataset_id": "demo-ledger-v1",
  "metric": "sales_change_by_product",
  "start": "2026-10-05T00:00:00Z",
  "end": "2026-10-12T00:00:00Z",
  "current_start": "2026-10-12T00:00:00Z",
  "current_end": "2026-10-19T00:00:00Z",
  "top_n": 5
}
```

仍需原 Idempotency-Key/授权或同源 Demo 入口。此请求没有 kind=agent，使用确定性工具，不消耗模型预算。不能传 as_of、product_id、任意 SQL 或额外维度。

```sh
.local/agent-venv/bin/python -m commerce_data.sales_change_cli examples/ledger/snapshot.json \
  --workspace demo-shop \
  --start 2026-10-05T00:00:00Z --end 2026-10-12T00:00:00Z \
  --current-start 2026-10-12T00:00:00Z --current-end 2026-10-19T00:00:00Z \
  --top-n 1
```

top_n=1 时下降榜只展示 P3，剩余 P2 的 -40 元必须留在“其他”；增长榜仍有 P1。输出含 totals、increases/decreases、other、reconciliation、queries、evidence 和 summary。缺退款表不会禁止此支付口径的分析。

## 复现验收

```sh
.local/agent-venv/bin/python experiments/sales-change/verify.py .local/sales-change-rerun.json
.local/agent-venv/bin/python experiments/single-agent/verify.py .local/sales-agent-rerun.json
.local/agent-venv/bin/python experiments/data-import/verify.py .local/sales-import-rerun.json
.local/agent-venv/bin/python experiments/web-demo/verify.py .local/sales-web-rerun.json
.local/wren-venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

新模块 21 项：12 项计算测试（含固定种子的 15 套独立 Python 汇总对照）、3 项图/证据控制、6 项 Java/HTTP/Worker 集成。原 Agent 42 项、导入 25 项、网页 11 项；全数据 70 项包含上述 12 项及导入适配器 12 项，不能将套件数量直接加为独立用例数。所有这些测试都不调用付费模型。

首次环境准备及本机范围见 [网页说明](../web-demo/README.md)、[数据导入说明](../data-import/README.md)。调研见 [007](../../docs/research/007-sales-change.md)，实测与边界见 [阶段 2e](../../docs/baselines/phase-02e-2026-11-06.md)。
