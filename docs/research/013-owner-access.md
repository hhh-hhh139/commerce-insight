# 013 · 单店主部署模式与登录保护

日期：2026-11-06；最终验证 2026-11-08。状态：工程与浏览器已验证，见 [阶段 3b](../baselines/phase-03b-2026-11-08.md)。目标是可部署的个人/单店主工作台，不新增注册、多组织、邀请、企业 SSO 或角色管理产品。实际公网 TLS 在部署阶段验收。

## 当前边界

本地 Demo 已限制回环地址、Host、Origin 与浏览器同源请求；直接 API 区分 user/worker bearer 并固定工作空间。它是刻意限定的本地入口，不是已经存在的公网身份系统。当前无法用一个带登录保护的页面在个人服务器上使用，这是部署能力缺口；不能简单把监听地址改成 0.0.0.0 就发布。

## 官方研究与选择

访问日期均为 2026-11-06，Spring Security 页面显示 7.1.2，随 Spring Boot 4.1.2 依赖管理。滚动网页未固定 commit。

| 方案 | 参考与收益 | 决策 |
| --- | --- | --- |
| 自写 JWT/前端保存 bearer | 能复用 API，但需自管登录、撤销、CSRF/XSS 与密码校验 | 不选；不把已有服务令牌发给浏览器 |
| OAuth/OIDC 托管登录或身份代理 | [Metabase JWT/SSO](https://www.metabase.com/docs/latest/people-and-groups/authenticating-with-jwt) 展示成熟身份集成思路，适合已有组织 IdP | 用户无部署资源/IdP；首版引入外部账户依赖不利于离线复现，后续多人需求再评估 |
| **Spring Security 表单＋服务端会话** | [表单登录](https://docs.spring.io/spring-security/reference/servlet/authentication/passwords/form.html)、[会话](https://docs.spring.io/spring-security/reference/servlet/authentication/session-management.html)、[CSRF](https://docs.spring.io/spring-security/reference/servlet/exploits/csrf.html)、[存储与 PasswordEncoder](https://docs.spring.io/spring-security/reference/servlet/authentication/passwords/storage.html)：框架负责认证、会话固定攻击防护、退出和 CSRF | 采用；一个部署者配置的 owner，密码哈希保存在本地私有运行目录，浏览器只持有 HttpOnly 会话 cookie |

框架不替我们建立租户业务模型。本版本沿用一个 `demo-shop` 工作空间，所有导入、任务和报告属于该部署，明确拒绝其他空间。需要多商户隔离时再实现主体→成员→工作空间授权及数据库隔离测试，不在简历上把固定空间拒绝测试写成成熟多租户。

## 实施边界

- 原本地模式继续保持回环限制，默认不启用公网访问或实时模型。新增 owner 模式必须显式开启，缺少合法密码哈希/Origin 等配置时拒绝启动。
- Spring Security 负责会话与表单认证，CSRF 对登录、退出与工作台写操作保持开启。前端从同源接口取得框架生成的令牌，不存密码或服务 bearer。会话过期时引导重新登录。
- 配置唯一外部 Origin 并严格校验 Host/Origin，忽略代理身份声明；公网模式使用 HTTPS 与 Secure cookie，HTTP 仅允许明确的回环开发地址。实际 TLS 在部署阶段验证，不能以本地 HTTP 测试冒充公网 TLS。
- Java /api 与 /internal 服务 bearer 入口继续限制回环 Host 和来源；网页登录不赋予 Worker 权限，用户令牌也不能领取任务。反向代理只转发公开页面/工作台路径。
- 登录采用一个有界、进程内全局尝试窗口作为单店主保护，避免创建无限 IP 状态；重启会重置它，不声称抵抗分布式攻击。无公开注册、找回邮箱或外部消息。
- 不引入外部 tracing、读取机器既有账户或模型 Key。部署者通过个人终端隐藏输入设置本应用密码；它不同于 GitHub、模型 API Key。

## 验收预先约定

匿名访问拒绝；错误密码拒绝；正确登录更换 session；无/错 CSRF 不可提交任务、上传或退出；同源正确请求完成；登录会话无法读取内部 API；错误 Host/Origin/代理头无权限；退出及重启使旧会话失效；登录尝试受限；配置不完整启动失败；原本地 Demo、API 与任务回归通过；浏览器完成登录→分析→退出。所有工程验收使用临时合成账户，不读取用户真实密码。

前阶段完整源码归档在 `.local/baselines/after-reliability-20261106/`。本模块实施前将另存包含本决策和边界探针的基线。
