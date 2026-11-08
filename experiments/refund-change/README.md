# 退款变化定位

先阅读 [调研与选型](../../docs/research/010-refund-change.md)。工具按成功退款**发生期**比较两期，不提供统计异常或退款原因判断，不把跨订单批次的比例称为退款率。

在项目根目录、已有项目内工具链时：

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python scripts/web_demo.py
```

打开本机页面，分析内容选“退款变化定位”，基期 2026-10-12 至 2026-10-19，对比期 2026-10-19 至 2026-10-26。日期为 UTC，结束不含。预期成功退款 50→10 元、变化 -40 元；退款记录 3→1 笔，去重订单 2→1 单；金额归属覆盖率 60%→100%。已关联商品 P1 -20、P2 -10、P3 +10 元；未归属部分 -20 元。分项与总额一致。

页面也支持已确认、含退款表的外部数据版本；无退款表的版本不开放该分析。自然语言入口使用同一受控工具 `query_refund_change`，与原严格的 `product_refund_amount` 分开：严格指标仍拒绝不完整商品归属，新工具则明确展示已知部分和未知部分。

直接运行 CLI（不调用模型）：

```sh
.local/agent-venv/bin/python -m commerce_data.refund_change_cli examples/ledger/snapshot.json \
  --workspace demo-shop --start 2026-10-12T00:00:00Z --end 2026-10-19T00:00:00Z \
  --current-start 2026-10-19T00:00:00Z --current-end 2026-10-26T00:00:00Z --top-n 1
```

Top N 按已关联商品变化的绝对值降序、商品编号升序排列。其他商品保留基期、对比期和差额，净差额为零也不丢弃。未归属金额始终单列。原订单来源按**各自期间**开始时间判断，非固定一批订单。

工程验收（临时 Java 回环端口、合成数据、脚本模型）：

```sh
.local/agent-venv/bin/python experiments/refund-change/verify.py .local/refund-check.json
.local/agent-venv/bin/python experiments/refund-evaluation/verify.py .local/refund-evaluator-check.json
```

真实模型专项入口已准备，但本阶段没有执行：

```sh
.local/agent-venv/bin/python scripts/evaluate_personal_agent.py --suite refund-change
```

仅由用户在个人终端输入 Key，不回显、不落盘；沿用原累计 5 元账本。2 道开发题、6 道保留题各两轮，门槛预先固定为每轮至少 80%、发布数字/引用错误为零。小样本共用合成账本，不能推断真实商家泛化。合并最终评测前无需为此反复输入 Key；本入口便于单独复现。

SQL、参数、规范化数据和实现 SHA 保留在结果中。Java 检查绑定及算术，不证明 Worker 所写 SQL 的真值；评测审计会重建 DuckDB 重算。业务事实来自当前成功退款快照，不提供退款状态历史或账户结算对账。
