# 外部账本导入复现

这是阶段 2d 的本机闭环：CSV → 显式映射 → 后台检查 → 人工确认 → 不可变版本 → 原分析任务。不调用模型猜测业务含义。研究与限制见 [006 决策](../../docs/research/006-data-import.md)，实测见 [阶段记录](../../docs/baselines/phase-02d-2026-11-06.md)。

## 操作演示

在个人项目根目录、已有项目内工具链的环境运行：

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python scripts/web_demo.py
```

打开 `http://127.0.0.1:18080/demo/imports.html`。停止用 Ctrl+C；H2 和历史数据留在项目 `.local/web-demo/offline/`。环境首次准备见 [Java](../task-loop/README.md) 与 [Agent](../single-agent/README.md)。

1. 展开“下载合成样例”，或直接选择项目 `examples/imports/renamed-major/` 下的 orders、items、refunds 三个 CSV；填写名称，声明公开/合成且无敏感内容，上传。
2. 结构检查完成后核对列建议。该样例声明币种 CNY、时间为无时区 UTC+08:00、完整覆盖截至 `2026-10-26T08:00:00`，三个表金额均为“元 / 币种主单位”。中文状态建议也要核对。
3. 点击“按此映射检查数据”。预期订单 5 行、明细 5 行、退款 6 行，无阻止发布的错误；一笔成功退款没有商品明细归属，商品退款指标不可用。
4. 勾选口径确认，再创建数据版本。“允许个人模型分析”默认不勾选；不影响本机指标查询。检查后改任意映射必须重新检查，旧 revision/检查摘要不能用于确认。
5. 使用此版本分析，默认 cohort_net，UTC 区间 `[2026-10-05, 2026-10-12)`、退款截至 `2026-10-19`（不含），结果 **230.00 CNY**。核查 SQL、参数、来源 SHA 和版本，下载 JSON。
6. 刷新或重启启动器，通过导入记录、任务记录恢复。已确认版本锁定；重新上传得到新版本。归档禁止新任务，已创建任务和报告仍保留。

第二套样例 `standard-minor` 使用英文列名、整数分、每个时间值带偏移；选择金额“分”、时间“自带时区偏移”、截至 `2026-10-26T00:00:00Z`。其规范表和业务答案与中文样例一致，来源文件和数据版本 SHA 不同。

不上传退款表时，必须保留“退款情况未知”的含义，只开放支付金额、支付订单数和含指定商品的订单金额。上传完整、只有表头的空退款表则属于用户声明没有退款记录，两者不能混淆。系统不能证明导出文件是否遗漏业务记录，完整性声明需要使用者负责。

## 文件与口径契约

| 表 | 必需业务字段 | 可显式不提供的来源列 |
| --- | --- | --- |
| orders | order_id、status、paid_minor、paid_at、currency | currency 可使用声明的单币种 |
| items | item_id、order_id、product_id、line_minor | 无 |
| refunds（整表可省略） | refund_id、order_id、item_id、status、amount_minor、refunded_at、currency | item_id 可为空/无归属，currency 可使用声明币种 |

字段名是内部语义，不要求 CSV 同名。`paid_minor` 等经过选定单位转换为整数最小单位；不能把销量、应付额或取消金额映射成支付/成功退款。明细 `line_minor` 是已分摊到明细的金额，不能直接拿未分摊的原商品标价相乘替代。

订单状态为 paid/pending/cancelled；退款为 succeeded/pending/failed。非支付订单的金额须为零、支付时间须空；非成功退款的成功时间须空。一个订单是一笔已确认支付视图，不支持多次支付、额外运费税费未分摊、拒付和状态历史重建。

UTF-8（可有 BOM）、逗号 CSV；每文件 128 KiB、2000 条记录、40 列、每格 4096 字符。编号保持字符串，前导零保留；不能含首尾空格。金额支持普通十进制，不接受公式、科学计数或自动舍入。币种仅 CNY/GBP/USD/EUR，均为两位小数，不混币种、不做汇率转换。时间必须显式含偏移或声明固定 UTC/UTC+08:00；不处理任意 IANA 时区和夏令时。

解析错误显示表、字段和 CSV **记录序号**（首条数据为 2，多行单元格时不等于物理行号）；收集总错误数和前 50 个定位。跨表校验目前只返回第一个业务错误。任何错误均阻止整批发布，不自动删行、去重或修金额。完全一致但错误的单位/状态/时间声明可能通过结构校验，因此检查通过不等于业务含义正确。

每个工作空间本机最多 50 次导入，归档也计数；每次导入最多 20 次映射检查。页面状态候选只展示前 20 个不同值，截断时提示，未知值会被检查拒绝；这不是通用清洗工具。大文件、XLSX、压缩包、远程 URL、数据库连接和国内平台现成连接器均不在本版入口。

## 后端边界与版本

Java 保存原文件、映射修订、确认摘要和不可变版本；Python Worker 执行 `import_check`，沿用原租约/超时/重试/取消协议。上传格式是有界 base64 JSON，源文件名不会变成本地路径。确认绑定 `revision + check_task_id + review_sha256`；原文件、规范快照、映射与检查代码均有来源指纹。任务创建时复制冻结快照，不用“当前最新数据”替换历史任务。

主要接口：`POST/GET /demo/api/imports`，`GET /demo/api/imports/{id}`，`POST /check`、`/confirm`、`/archive`，`GET /demo/api/datasets`。完整认证接口在 `/api/workspaces/demo-shop/` 下使用原 user bearer；worker 只调用 `/internal/`。普通任务接口不允许用户创建 import_check。demo 接口要求原同源标头，默认关闭、仅回环，没有用户身份认证。

导入数据只允许公开/合成内容；原文件和报告预览保存在 ignored H2，归档不是删除，也没有保留周期清理。导入检查子进程不继承个人模型 Key。即使确认了模型许可，也需个人终端 `scripts/web_demo.py --live` 才能在网页运行模型；沿用既有共享预算和价格日期门槛。离线与实时模式 H2 分离，数据版本不自动互通，需要在对应模式重新导入。当前没有通过新导入网页调用真实模型，本阶段是零付费工程验收。

## 复现检查

```sh
.local/agent-venv/bin/python experiments/data-import/verify.py .local/data-import-rerun.json
.local/agent-venv/bin/python experiments/single-agent/verify.py .local/data-import-agent-rerun.json
.local/agent-venv/bin/python experiments/web-demo/verify.py .local/data-import-web-rerun.json
.local/wren-venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

首项共 25 项（12 适配器 + 13 JVM/HTTP/Worker/图执行），原 Agent 42 项、网页 11 项。全数据测试 58 项包含同一批 12 个适配器测试，不应将四套数量直接相加为独立用例数。最后一项需包含 XLSX 的原完整数据环境；可按数据测试说明配置，不要求另引入 Wren 到产品运行。

生成/打包样例：`python3 examples/imports/generate.py`、`python3 scripts/package_import_examples.py`。只使用项目已有合成账本，不调用模型、不下载用户数据。静态资源变动后重新 build。

当前页面只验收功能；根据用户要求，完整功能稳定后另做整体视觉与交互重构。
