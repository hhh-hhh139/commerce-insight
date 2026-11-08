# 报表差异核对

本模块检查两份**待核查报告声明**与同一授权账本的差异。不是支付网关结算、银行对账，也不读取任意表格、图片或报告中的 SQL。决策依据见 [011](../../docs/research/011-report-reconciliation.md)。

在个人项目根目录，已有工具链时运行：

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python scripts/web_demo.py
```

打开本机工作台，选“报表核对”：

1. 选择已确认的数据版本。
2. 上传两份声明模板，或本系统“下载报告 JSON”得到的**成功单指标任务**。支持外层 `display_source/task` 包装和原任务 JSON；Agent 多证据报告、商品贡献和退款变化结构化报告不能假装单值报表。每份文件 ≤100 KB。
3. 核对预览、声明的源版本、指标、期间、退款截止及单位，勾选确认后运行。改变文件或账本后需要重新确认；历史任务恢复也不会自动确认新运行。
4. 分别看“声明值／账本重算／声明减重算”，再看两报告的桥接、口径差异和证据。未知来源时会显示“所选账本参考”，不会给出已验证偏差或金额桥接。

可直接点“载入差异示例”，切到内置合成账本并载入 280 元支付报表与声明 240 元的批次净额报表。重算后净额是 230 元，因此退款口径解释 -50 元，另 +10 元仍是报表偏差，不能说全部解释。

文件示例位于 `examples/reconciliation/`：

- `paid-report.json`：28000 分。
- `cohort-report.json`：23000 分，与该支付批次截至 10/19 的退款口径相符。
- `cohort-report-discrepancy.json`：故意声明 24000 分，必须被识别为偏差。
- `source-unknown-report.json`：未知原始数据版本，即使数字恰好相同也不得宣称已验证。

模板必需字段为 `schema_version/name/query/reported_value/currency/amount_scale/result_unit/metric_version/source_sha256`。指标仅现有八个标量；查询使用带时区的左闭右开区间，精度不超过微秒。`reported_value` 为整数最小单位，不是带小数的元。网页拒绝超过 JavaScript 精确整数范围的声明值，后端完整整数上限为绝对值 10^17；不要让网页静默舍入。源哈希未知必须填 null，不要编造；声明哈希只是绑定，不是可信签名。

四种结果：

| 结果 | 含义 |
| --- | --- |
| `matched` | 声明口径相同，左右各自与重算一致 |
| `explained` | 声明口径不同，但左右各自与重算一致 |
| `discrepancy` | 至少一份声明偏离重算；两边同错同量也属于此类 |
| `needs_review` | 来源、定义、数据或可比单位不足，不生成金额桥接 |

有效桥接是“左声明 − 左报告偏差 + 重算查询差异 + 右报告偏差 = 右声明”。多条件同时改变时只展示联合重算差异，不虚构独立因素的贡献。跨来源版本只做当前快照的参考计算，不声称验证另一快照。

工程验收：

```sh
.local/agent-venv/bin/python experiments/report-reconciliation/verify.py .local/reconciliation-check.json
```

该命令覆盖数据边界、真实 Java/Worker/DuckDB、人工确认、不可比情况、幂等、重启、取消和发布篡改，零模型调用。此阶段以确定性任务完成核对；尚未提供 Agent 自主读取已上传报表的上下文工具，不应把本阶段写成真实模型核对能力。后续流程优化将在固定上下文上评估是否值得让 Agent 选择此工具。
