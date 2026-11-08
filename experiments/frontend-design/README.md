# 前端改造复现

在项目根目录运行（项目内工具链已准备时）：

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python scripts/web_demo.py
```

打开 `http://127.0.0.1:18080/demo/index.html`。默认不调用模型；Ctrl+C 停止本次服务。不要将本机 Demo 直接暴露公网。

手动验收：

1. 默认选择商品贡献。运行 2026-10-05—12 与 12—19，得到 280 → 150 CNY、P3 -100、P2 -40、P1 +10。打开 SQL 证据并下载 JSON，图表不替代原数据。
2. 进入任务记录，打开另一日期的任务；核对表单与报告一致，后退回到列表。历史样例有单独标识；离线自然语言提交禁用。
3. 进入数据管理，上传 `examples/imports/standard-minor/` 三份 CSV。时间选择自带偏移，三表金额单位选择分，数据截至 `2026-10-26T00:00:00Z`，核对状态和字段后检查、确认。模型许可默认不勾选。
4. 用该版本查询订单批次净额：支付区间 10-05—12、退款截至 10-19，应为 230.00 CNY。商品退款缺归属时保留能力限制。
5. 在 1440 px、696 px、390 px 检查布局、导航名称、数据说明、键盘焦点和滚动；窄屏明细表只在自身区域横向滚动。
6. 可用本次新建的合成版本验证归档：旧任务仍可读，新分析必须另选可用版本。不要归档用户想保留的版本来测试。

已有工程回归：

```sh
.local/agent-venv/bin/python experiments/web-demo/verify.py .local/frontend-web-check.json
```

该 11 项检查覆盖服务器网页契约；图表、焦点和历史条件恢复仍须按上述浏览器步骤验收。实际本轮结果见 [阶段 2f](../../docs/baselines/phase-02f-2026-11-06.md)。
