# 本地分析工作台：调研与决策

日期：2026-11-06。范围：个人项目、本机合成数据演示。调研完成，结果另见阶段 2c。

## 问题与开发前基线

阶段 2b 已有 Java → Python Worker → LangGraph/DuckDB → Java 报告，但 `task_demo.py` 只执行一项固定指标并输出 JSON，随后关闭服务。没有网页、任务列表或面向使用者的证据入口。真实模型 17 份历史报告存在评测文件中，不能冒充当前新运行的结果。

源码快照：`.local/baselines/before-web-demo-20261106/source.tar.gz`，106 文件，SHA-256 `02e4bf5fd5efcd55935f960d293ff80401ac71a0269186591b8d5026b09f467f`。原始逐文件清单同目录保存。

## 官方资料与可借鉴机制

以下均于 2026-11-06 查阅，滚动文档未固定版本。它们支持产品公开描述，不证明内部实现或我们的需求已获得用户验证。

| 产品 / 文档 | 文档声明 | 本项目借鉴与边界 |
| --- | --- | --- |
| [Wren AI](https://docs.getwren.ai/oss/overview/how_wrenai_works) | UI 提问并展示答案、结果预览与图表；业务上下文和查询执行分层 | 同屏问题、结果、口径与证据；保留已有命名指标，当前不引入任意 Text-to-SQL。页面位于 GenBI Classic / Sunset 文档区，只参考交互与职责分工 |
| [Metabase](https://www.metabase.com/docs/latest/dashboards/interactive) | 图形查询结果支持下钻；SQL 问题交互能力有所不同 | 数字卡片可展开来源、SQL 与参数；当前没有原始订单行下钻，不宣称复制完整 BI |
| [Dify Logs](https://docs.dify.ai/en/cloud/use-dify/monitor/logs) | 浏览运行记录，查看输入输出、状态、token 与执行轨迹 | 持久任务历史、状态事件和公开 usage；不展示私密 checkpoint 或推理过程 |
| [Spring Boot Servlet Web](https://docs.spring.io/spring-boot/reference/web/servlet.html) | 原生托管 classpath `/static` 静态资源 | 页面随业务 JAR 发布，同源调用，不引入额外前端运行服务 |
| [MDN Fetch Metadata](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site) | 浏览器标记请求来源关系，服务端可拒绝跨源请求 | 仅本机演示入口限制 Host、Origin、Sec-Fetch-Site 和自定义请求头；这不是用户认证 |

## 最小方案比较与决策

1. 保持 CLI：可靠、已有验证，但使用者不能交互或找回历史。
2. Streamlit/Gradio：快速搭出 Python 演示；若直接执行 Agent 会绕过 Java 业务任务，若只包 API 则多一个服务。当前交互规模用不到它们的主要收益。
3. React/Vite + Java API：适合后续导入映射、多页面和复杂状态，但此时增加构建依赖与前端工程维护。
4. **采用 Spring 静态 HTML/CSS/JS + 同源 API + 轮询**：复用任务闭环，零新增第三方前端依赖。将 UI 请求集中封装，后续框架迁移不改任务契约。复杂度增长时重评 React，而非坚持无框架。

本版交付：指标查询表单、自然语言入口（启动时显式开启）、运行/取消、报告与证据、下载公开报告、分页历史、刷新恢复、历史真实模型样例（明确标识日期和历史来源）。金额计算保留在 Python，前端只按返回单位格式化。图表仅用于同口径两期比较，不生成因果结论。

默认离线：执行真正的 Java/Python/DuckDB 查询，不调用模型。历史报告只读且不写入本次任务表。实时模式由操作者在个人终端 `--live` + getpass 启用，共用原日期的 5 元预算账本；本轮开发不新增真实付费调用。不能把接口联通测试当新一轮模型效果评测。

## 本地入口边界

默认服务禁用 `/demo/`。专用启动器开启它时，只接受 127.0.0.1 的精确 Host 和回环连接，并强制已审查样例 SHA。Demo 没有用户身份隔离，同机进程可访问，不能公开部署或接私有数据。`/api/` 与 `/internal/` 继续分别使用原有 bearer 角色；网页不接触这些 token 或模型 Key。无 cookie，无 CORS 放行，修改请求必须同源。静态资源固定白名单，CSP 禁止第三方脚本/嵌入，业务文本使用 textContent。

下一阶段部署前必须替换为正式认证、工作空间授权、TLS 和生产数据存储，不能靠“改监听地址”发布此模式。

## 预先约定的验收

- 浏览器提交 cohort_net 得到 230.00 CNY，能展开 SQL/参数/哈希，刷新找回同一任务。
- 历史分页无重复、取消落库且不可复活；缺商品退款归属展示失败原因。
- 真实模型历史示例显示 280 → 150、差额 -130、-46.43%，同时标记历史日期；拒答/澄清也能呈现。
- 默认实时 Agent 被服务端拒绝，不只禁用按钮；跨源、错 Host、无头请求、角色混用被拒；错误长度输入受限。
- 原任务与 Agent 工程回归通过；相关前端在实际浏览器完成操作检查。
- 不声称 UI 提高了模型准确率或给真实商家节省时间。实际结果与局限追加到阶段记录。
