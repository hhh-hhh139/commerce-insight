# 首次个人发布与干净 Linux 验收

日期：2026-11-09。当前唯一目标为个人公开仓库 `hhh-hhh139/commerce-insight`。

## 问题与基线

此前已验收 macOS 上的 Java/Python、PostgreSQL 和 TLS，但不能据此声称 Linux 容器可部署。
首次发布提交 `8769463199dc9afb4071d88019f97413e571e214` 的真实 GitHub hosted CI 暴露两个环境差异，保留失败，不把重跑结果覆盖原记录：

1. [运行 1](https://github.com/hhh-hhh139/commerce-insight/actions/runs/42554876042)：两种镜像构建成功，Agent 42/42、导入 25/25；上下文 12 项中 1 项错误。Mock HTTP 的序列化测试读取了真实日期，11 月 9 日触发生产价格复核门禁。
2. 修复第一项后的 [运行 2](https://github.com/hhh-hhh139/commerce-insight/actions/runs/42555889381)：离线套件全部通过，Compose 数据库和应用健康；首个 HTTPS 请求出现 `TLSV1_ALERT_INTERNAL_ERROR`，尚未进入登录验收。

可恢复源码基线分别为上述提交和 `17d5fac2194fdc2863bf8da32158bd9f2d4fafe6`。本地保留每轮 artifact ZIP、SHA-256、原始 JSON/日志与修复前文件摘要；后续阶段记录列出可公开的实测结果。

## 官方依据与方案比较

这是现有组件的验收缺陷，沿用部署模块对 Metabase、Supabase、Caddy 的比较，不为两个测试问题重新引入部署平台。参考 [TLS 原选型](017-tls-deployment.md)。本日复查以下一手来源，文档为滚动版本：

- [Python unittest.mock：where to patch](https://docs.python.org/3.12/library/unittest.mock.html#where-to-patch)：应在被测试代码实际查找对象的命名空间替换依赖。
- [Caddy default_sni](https://caddyserver.com/docs/caddyfile/options#default-sni)：在客户端没有发送 SNI 时提供明确名称。
- [Caddy 指定证书](https://caddyserver.com/docs/caddyfile/directives/tls)：手动证书和私钥配置。
- [Caddy 官方问题记录 6364](https://github.com/caddyserver/caddy/issues/6364)：IP 访问与 NAT 下的相同握手现象；该讨论提供排查线索，最终结论由实际组件复现确认。

| 问题 | 备选 | 选择与理由 |
| --- | --- | --- |
| Mock 测试受真实日期影响 | 放开生产价格检查；改全局时钟；只固定该测试的价格时钟 | 选择局部 `patch("commerce_agent.budget.datetime")`。该用例验证 SDK 内容序列化，真实价格日期不是变量；生产日期门禁和原账本保持不变 |
| IP 证书经过地址映射后不可选 | 改用 DNS 测试域名及证书；显式默认 SNI；关闭证书校验 | 选择只在 CI 临时 Caddyfile 中配置 `default_sni 127.0.0.1`。保留原 IP Origin、SAN、路由及客户端校验；DNS 方案有效但需要同时改多项固定输入。关闭校验无法解决服务端选不到证书的问题，也失去验收意义 |

## 机制与验证

第一项：测试只在 `commerce_agent.budget` 查找日期时固定为已核验日期。SDK 请求仍走 HTTP Mock，既无模型请求，也不更新真实价格核验日期。局部套件从 11/12 变为 12/12，本机耗时 17.751 秒；随后 Linux 同一套件 12/12。

第二项：IP 客户端不发 SNI；Caddy 无默认名称时使用连接本地地址匹配证书。原 macOS 测试直接监听 `127.0.0.1`，恰好匹配 SAN；Docker 映射后目的地址不同。用实际 Caddy 2.11.9 监听 `::1`、客户端仍验证 `127.0.0.1`，可在不修改系统网络的情况下复现这一差异：原配置握手失败，增加默认名称后 HTTP 200，不信任证书或验证错误 IP 仍报 `CERTIFICATE_VERIFY_FAILED`。这组回环实验不是 Docker 结果；容器结果以独立 CI 为准。

复现入口：在已通过 `scripts/bootstrap_caddy.py` 准备 Caddy 的个人 macOS 环境执行：

```sh
.local/agent-venv/bin/python experiments/deployment/verify_tls_selection.py
```

仅使用本项目 `.local/ci-tls-probe`、回环 IPv6、临时证书与进程，结束回收进程；不安装 CA、不改系统信任、不访问 Docker daemon。证书/私钥和原始日志不发布。

修复分别发布为 `17d5fac` 和 `f190be0`；后者只修改 CI 证书选择。部署域名/ACME、付费模型、商家试用等仍按各自验收门槛记录，不由 CI 工程通过推定。

## 第三轮：只读根目录下的临时数据库

[运行 3](https://github.com/hhh-hhh139/commerce-insight/actions/runs/42556666416) 已通过 TLS、未登录拒绝、会话轮换、登录和 CSRF；真实 Worker 在分析任务返回 `analysis_rejected`。检查发现所有确定性账本 CLI 都在 `.local/ledger-runs` 创建临时 DuckDB，但 Compose 只为 `.local/task-loop` 提供 tmpfs，应用根文件系统只读。此前离线套件在可写验收容器运行，不能发现这条部署路径缺失。

实际文件系统反例用非 root 用户将独立测试根目录设为不可写，只将 CLI 的源码位置指向该测试目录：未提供临时目录时 exit 2；仅预置可写 `ledger-runs` 后真实 DuckDB 返回 28000 分，临时数据库清理成功，父目录继续不可写。程序见 `experiments/deployment/verify_readonly_scratch.py`；它没有模拟文件系统或计算，但也不是 Docker 的只读挂载实验。

本日复查 [Docker tmpfs 官方说明](https://docs.docker.com/engine/storage/tmpfs/)（滚动版本）。比较：去掉根目录只读过度扩大写入范围；永久 volume 会保留无需恢复的查询中间文件；修改全部 CLI 到其他目录会扩大代码改动。选择给既有目录增加 UID/GID 1000、0700、128 MiB tmpfs，并在 Compose 解析验证器中检查。tmpfs 占用容器内存限额，可能被 OS swap，不能宣称永不落盘；业务报告、数据版本和预算仍使用原持久化状态。

源码基线 `f190be0`，挂载修复 `8855db6`。官方 Compose 5.7.0 的正式/CI 两组配置检查均通过，0.102 秒，未连接本机 Docker。最终容器运行结果见阶段 5f，不能仅凭配置检查认定任务恢复成功。

## 交付审查：业务专项在 Linux 的覆盖缺口

2026-11-09，基线 `1d9fee876316604d070102e640ffa6131c845b75`、tree `db1e210d74eab8f436b60cbbf7bcb70ade23417a`。第 5 轮已实际通过独立容器恢复；读取 `verify_clean_ci.py` 的执行列表及下载产物后确认：销售变化、退款变化、报表核对、资源可靠性和 owner 登录的独立专项没有进入 Linux CI。这不是已观察到的业务故障，而是交付证据缺口。上下文题集和 TLS 冒烟不能替代这些专项的业务 API、篡改拒绝与竞态断言。

本日读取以下官方滚动文档：

- [Metabase 开发与测试](https://www.metabase.com/docs/latest/developers-guide/devenv)：区分隔离业务逻辑的单元测试与模拟用户流程的端到端测试。可借鉴分层覆盖，不把一种测试名称扩写为全部验证。
- [Supabase 测试概览](https://supabase.com/docs/guides/local-development/testing/overview)：同时介绍数据库测试和通过应用客户端验证策略的应用层测试，支持把真实接口边界纳入 CI。
- [GitHub Actions matrix](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations)：适合多个操作系统、语言版本或任务配置；可以控制并发和失败行为，但并不自动补足测试覆盖。

比较保留现状（无法关闭缺口）、拆成矩阵任务（需要重复构建或分发镜像）、在同一构建镜像中分别启动容器运行已有专项。选择最后一种：本项目当前只承诺一组 Linux/Python/Java 版本，原完整验收约 4 分钟，新增五套无需新框架或模型服务。每套独立容器、单独 JSON，失败保留日志，后续再运行 Compose TLS/恢复流程；不把本机再次通过当作 Linux 证据。

预定验证：现有 Agent 42、导入 25、上下文 12、模型故障 9 和备份路径继续通过；新增销售、退款、核对、可靠性、owner 五套必须实际执行并通过，结果记录 commit、镜像 digest、题/测试源 SHA 和耗时。它们仍是无真实模型的工程测试；视觉交互沿用有日期的人工浏览器记录，不改称自动 UI 验收。若耗时或资源成为问题，再基于测量考虑并行。实施结果另记本日交付审查。

### 实际镜像软件清单

同一审查发现 `THIRD_PARTY_NOTICES.md` 已要求已验证发布附带镜像 digest 和软件包清单，但原 artifact 只有 digest，现有许可清单明确来自 macOS，不能当作 Linux 系统包清单。基线为 `285ac29f3c651d444f48630ca85e34334844539f`，原文件 SHA 和 artifact 文件列表保存于本地审查记录。

本日核对 [Python 3.12 importlib.metadata](https://docs.python.org/3.12/library/importlib.metadata.html)、[Debian dpkg-query](https://manpages.debian.org/bookworm/dpkg/dpkg-query.1.en.html)、[Alpine apk](https://docs.alpinelinux.org/user-handbook/0.1a/Working/apk.html) 官方文档。比较静态复制本机清单、引入完整 SBOM 生成工具、用镜像中已有的包数据库和标准库记录版本。当前没有标准化 SBOM 下游消费需求，选择第三种：从本轮实际 app/maintenance/PostgreSQL/Caddy 镜像读取软件版本，记录 image ID、发行版包、Python distribution、jar 内依赖及哈希。独立容器无网络、不挂应用数据或 secret，不操作宿主软件；没有新增运行时依赖。

这份 JSON 是已安装软件/应用依赖的复现清单，不声称枚举每个复制的二进制内部依赖、完成许可法律审查或漏洞扫描。验证其 Python 版本与锁文件相符、关键 Java 依赖存在、OS 包条目非空，再随实际业务验收一起留存；许可说明仍保留各依赖原始声明。
