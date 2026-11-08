# 本地网页 Demo

## 运行

在个人项目根目录执行，使用此前已安装的项目内 Java 工具链和 Python Agent 环境：

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python scripts/web_demo.py
```

打开 `http://127.0.0.1:18080/demo/index.html`。必须使用 `127.0.0.1`，不能换成 localhost、其他域名或局域网 IP。首次环境准备见 [Java](../task-loop/README.md) 与 [Agent](../single-agent/README.md)。这不是尚未验证的“一条命令干净机器部署”。

默认不需 Key，不调用任何模型：

1. 保持默认口径和日期，点击运行，得到 **230.00 CNY**。
2. 展开证据，核对 SQL、参数和快照哈希；下载 JSON 保留报告。
3. 页面刷新保留 URL 中的任务 ID；记录来自 Java/H2，不依赖浏览器缓存。
4. 选择商品退款金额、P1、10-12 至 10-19，看到缺少商品归属的失败原因。
5. 点击 Agent 历史样例，查看比较、澄清、拒答与原评测失败；明确区分“查看过去报告”和“现在运行”。
6. 进入“数据管理”，可上传公开/合成账本 CSV，显式映射、检查、确认后选择该版本分析。详见 [阶段 2d 导入说明](../data-import/README.md)，包括两套样例、能力限制和文件上限。

任务执行中可取消，已经完成的任务不能取消。断网导致提交响应丢失时，同一页、相同输入的再次提交复用幂等 key；刷新页面会丢失内存中的未确认提交 key，应先查任务记录，当前未提供浏览器崩溃后的提交恢复保证。

Ctrl+C 停止本次服务与 Worker 进程组，数据保留。默认运行目录 `.local/web-demo/offline/`，实时模式 `.local/web-demo/live/`，各有独立 H2。不会清空或改写历史评测数据库。端口占用可选择 `--port 18081`；不终止占用该端口的未知进程。同一模式不能启动两个启动器。

## 可选实时 Agent

仅由你在个人终端启动：

```sh
.local/agent-venv/bin/python scripts/web_demo.py --live
```

启动器用 getpass 隐藏输入个人 DeepSeek Key，只传入 Worker 的内存环境；不使用继承的 Key，也不把 Key 交给网页或 Java。使用同一个 `.local/single-agent/personal-budget-2026-11-06.json` 总预算账本，不另开 5 元额度。当前价格只核验至 2026-11-06，跨日会拒绝执行，须重新调研价格与预留规则后更新。

默认演示与实时模式只有同源、回环入口限制，**没有用户身份认证**。同机其他进程可访问；只用于已审查样例和明确声明的公开/合成导入。不能暴露到公网、通过代理转发或导入私有数据。原 `/api/`、`/internal/` bearer 边界保持。导入版本还需单独允许模型使用；两种运行模式的数据库独立，需要在对应模式导入。正式部署要单独实现身份、工作空间授权与生产存储。

本阶段没有新增真实付费模型调用。实时模式的 Java 接收路径、现有 Agent SDK 和离线执行回归有验证；“新网页提交 → 新的真实模型响应”尚未再次验收，不能用历史样例代替该项。

## 复现检查

```sh
.local/agent-venv/bin/python experiments/web-demo/verify.py
.local/agent-venv/bin/python experiments/single-agent/verify.py .local/web-demo/agent-regression.json
```

新增 11 项检查启动真实 Java 服务与 DuckDB；原 42 项检查含 SDK 模拟响应与真实 LangGraph，均不调用外部模型。11 项覆盖静态资源、来源限制、默认关闭实时入口、体积边界、幂等、分页、重启恢复、取消 fencing、失败原因、历史样例保真及非样例快照拒绝启动。

静态资源随 JAR 打包；修改页面后重新 build 并重启，浏览器刷新后才能看到最新版本。`scripts/package_demo_examples.py` 仅从已经审查的真实评测记录打包 5 个样例，包括 1 个分类失败；打包不会执行 Agent。

调研与取舍见 [模块研究](../../docs/research/005-web-demo.md)，阶段结果见 [2c 状态](../../docs/baselines/phase-02c-2026-11-06.md)。
