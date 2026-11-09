# 交付审查：可部署版本与剩余验证

日期：2026-11-09。此记录更新[11 月 8 日审查](release-audit-2026-11-08.md)，不覆盖历史事实。当前源码已发布到个人 [hhh-hhh139/commerce-insight](https://github.com/hhh-hhh139/commerce-insight)，Java 业务后端与 Python LangGraph 分工保持不变。

**结论：主要业务功能已有工程和浏览器证据，Linux 构建、登录、分析、重启与独立空卷恢复已通过；完整项目的最新真实模型与试用门槛尚未完成。** 用户已决定先完成可部署版、暂缓核对未知模型费用，因此本轮不调用付费模型、不清除预占、不购买部署资源。

## 本轮发现与改进

审查基线为 `1d9fee876316604d070102e640ffa6131c845b75`。读取原计划、源代码、阶段 JSON 和个人 GitHub 实际运行产物，发现两个具体缺口：

1. 五套业务/控制专项已经在本机验证，但没有进入 Linux CI。加入同一构建镜像、分别运行独立容器的既有套件，无新测试框架或运行时依赖。
2. 第三方声明要求镜像软件清单，现有清单来自 macOS；增加实际镜像内的 OS 包、Python distribution、Java jar 依赖与版本记录。它是复现清单，不是完整 SBOM、漏洞扫描或许可法律结论。

问题证据、Metabase/Supabase 的测试分层、矩阵与同镜像方案比较、包清单的官方依据及选型见[研究 019](research/019-clean-linux-ci.md)。本轮不修改业务计算、Agent 默认架构或模型预算。可回退到上述 Git 基线；原始下载产物及文件哈希保存在项目忽略目录，公开摘要保留实际运行链接。

第 6 轮对 `285ac29f3c651d444f48630ca85e34334844539f` 的[实际 Linux 验收](https://github.com/hhh-hhh139/commerce-insight/actions/runs/42586103356)通过，Job 耗时 6 分 2 秒。新增销售 21、退款 21、核对 19、可靠性 12、owner 登录 8 项均通过，原 Agent 42、导入 25、上下文 12、模型故障 9 项以及 TLS/重启/恢复继续通过。套件有继承和不同检查层次，不相加声称独立用例数。JSON 中部分 `date` 是历史脚本常量，实际执行日期以 `release.json` 的起止时间和 CI run 为准。

第 7 轮对 `b432ed43ad620e6d84356c06f269c3fed0583a2e` 的[完整 Linux 验收](https://github.com/hhh-hhh139/commerce-insight/actions/runs/42586995399)也通过，Job 耗时 7 分 11 秒，九套专项、备份路径、TLS、重启与独立恢复均通过。实际镜像含 49 个锁定 Python 依赖且版本一致，另外安装 pip；Java jar 内 52 个依赖。application/maintenance/PostgreSQL/Caddy 分别记录 105/159/147/32 个 OS 包条目。这是本次构建的观测值，不承诺未来系统包仓库或构建过程逐字节一致。

[两轮实际结果、原始文件 SHA 与验证源码摘要](probes/delivery-audit-2026-11-09.json)和[实际镜像软件清单](probes/image-inventory-2026-11-09.json)已保留。下载包 `13660181112` 共 34,825 字节，SHA-256 为 `d6da13bb69936137d774d2ec419a69f13f2a79b1b78a479c47cd9a71d78b39d5`。公开软件清单与 CI 原始 JSON 字节一致；没有 API Key、生成的测试密码、证书私钥或用户数据。两轮均无真实模型请求；耗时差异没有控制 runner/网络条件，不能推定为清单采集本身的开销。

## 原要求逐项核对

| 需求 | 已核对的证据 | 当前适用范围 |
| --- | --- | --- |
| 个人项目隔离、可追踪研发 | [账户边界](account-boundary.md)、[研发流程](engineering-process.md)、研究/ADR/阶段记录；个人仓库提交及 CI | 本轮仅个人项目、指定公开仓库和个人 hosted runner；未使用公司仓库、凭据或本机 Docker |
| Java 业务 + Python Agent | [任务协议](adr/002-http-durable-task-loop.md)、[LangGraph 决策](adr/003-langgraph-single-agent.md)、Linux Agent 42 项 | Java 管业务状态/授权/发布，Python 管推理/工具/checkpoint；不是框架内演示代替业务链路 |
| 外部导入与映射确认 | Linux 导入 25 项；[浏览器导入](probes/data-import-browser-2026-11-06.json) | 订单/明细/退款 CSV；两种字段布局、金额单位、状态和时区；确认后冻结版本，默认禁止发送给模型 |
| 销售变化 | Linux 销售 21 项；[历史真实模型专项](probes/sales-evaluation-live-2026-11-06.json) | 已支付金额商品贡献与总额勾稽；不推断价格/销量或经营因果；真实模型结果只属于阶段 2h |
| 退款变化 | Linux 退款 21 项；[页面验收](probes/refund-browser-2026-11-06.json) | 按退款发生期，披露原订单支付期、商品归属覆盖与未知金额；当前真实模型专项未完成 |
| 报表差异核对 | Linux 核对 19 项；[页面与导出重导入](probes/reconciliation-browser-2026-11-06.json) | 结构化声明、人审确认、授权快照重算、可解释变化及残差；不承诺任意 PDF/Excel 理解 |
| 工作台、历史、证据与移动布局 | [11 月 6 日实际浏览器记录](probes/frontend-design-browser-2026-11-06.json)，以及后续退款/核对/登录页面记录 | 人工浏览器验收，有日期与截图哈希；本轮没有重跑视觉测试或新增自动浏览器套件 |
| 可靠性、限额和观测 | Linux 可靠性 12 项与 Agent/任务套件；真实幂等、租约、心跳、取消、持久截止与输出限制 | 有限单实例边界；没有吞吐、集群扩容或高可用承诺 |
| 登录、权限与 TLS | Linux owner 8 项，Compose 实际 TLS/CSRF/内部路径验证；[浏览器登录](probes/owner-access-browser-2026-11-08.json) | 单店主会话模式；owner 专项本身不是 TLS 测试，TLS 由另一实际 Compose 流程验证；无多租户/公网 ACME 结论 |
| 预算与模型故障 | Linux 故障 9 项、累计预占与恢复检查；[真实失败与修复](baselines/phase-04b-2026-11-08.md) | 两笔未知预占继续保留；保守占用 4.525418 元不是已扣费用；本轮个人账本 SHA 未变 |
| 单 Agent / 上下文 / 专家对照 | Linux 上下文 12 项；固定题集、门槛与[实验入口](../experiments/context-evaluation/README.md) | 工程通过；真实对照未开始；默认 `full` 单 Agent，不宣称摘要节省 token 或专家胜出 |
| 存储、迁移、备份与恢复 | [本机迁移](probes/postgres-migrations-2026-11-08.json)、[容器恢复](baselines/phase-05g-2026-11-09.md)、本轮相同流程回归 | PostgreSQL/Flyway，停写、同实现、空库/空卷恢复；9 个状态文件一致，任务仅一次成功，checkpoint 与预算连续；非热备/PITR/异地灾备 |
| 干净复现与可部署版本 | 个人 CI 构建和完整工程验收；[部署手册](deployment.md)；镜像 digest 与实际软件清单 | 默认确定性离线入口可运行，不需模型 Key；实时模型容器部署尚未验收 |
| 求职展示和价值验证 | [四点简历、前后对比与演示](project-story.md)、[JD 映射](job-alignment-2026-11-05.md)、[试用协议](trial-protocol.md) | 可以讲清取舍、失败和实测；JD 不是需求证明，没有真实商家使用量或节省工时数据 |

## 剩余事项与继续条件

- **当前真实模型**：先取得两次未知调用的可核对用量/扣费证据，再按原累计 5 元约束制定结算与评测；Key 仍只由本人在终端输入。用户本轮选择暂缓，不反复催问，也不自动释放预占。历史模型样例可展示，但不能称为当前实时结果。
- **分析方案对照**：当前退款回归及 full/compact/双专家受控实验尚缺真实结果；同模型、工具和预算下达到预定门槛才切换默认方案。工程测试或模型自评不能代替。
- **公网和试用**：用户暂无资源和试用者；先交付可部署版本，未来再基于实际免费服务限制选择托管，并邀请独立试用者。未执行公网上线、域名续期或真实商家试用。

主要功能实现与工程验证已形成闭环，但上述外部验证条件仍在，因此不将原 Goal 标记为全部完成。也不为填满清单继续引入消息中间件、更多 Agent 或无业务依据的新功能。
