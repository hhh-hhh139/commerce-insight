# TLS 代理与单店主会话联验

日期：2026-11-08。实施前：已有 Spring Security/CSRF 与 Secure cookie 配置检查，没有经过真实 TLS 代理的登录、分析和退出证据。不能把设置 `secure=true` 当成 HTTPS 已部署。

同类与官方来源（当日访问，滚动文档）：

- [Metabase 自托管安全](https://www.metabase.com/docs/latest/installation-and-operation/security-best-practices-self-hosting)：受控反向代理、限制应用端口、TLS 与正确外部 URL。借鉴部署边界；不照搬其配置名。
- [Supabase 自托管 HTTPS](https://supabase.com/docs/guides/self-hosting/self-hosted-proxy-https)：Caddy 与 Nginx＋Let's Encrypt 两条方案；真实公开证书需要域名/DNS和可达端口。
- [Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)、[指定证书](https://caddyserver.com/docs/caddyfile/directives/tls)、[反向代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)：证书管理、手动证书路径、代理 Host/转发头行为。
- 固定测试二进制 [Caddy 2.11.9](https://github.com/caddyserver/caddy/releases/tag/v2.11.9)，官方发布 SHA-512 校验，解压仅项目 `.local/caddy-toolchain`。不注册系统服务。

选择 Caddy：此单实例没有既有个人反向代理，短配置和证书生命周期管理更合适。Nginx 成熟且适合已有运维环境，当前引入独立续期流程并无明确收益。Java 直接 TLS 也可行，但将部署证书、边界路由放在代理更容易独立管理。不会以此宣称性能提升。

本地验收选择明确提供临时证书并禁用自动 HTTPS/CA 安装；仅测试客户端显式信任。不开公网监听、不改系统信任、不申请真实证书。代理只放行登录/会话/工作台路由，内部 bearer API 不经代理暴露。Java 保持固定外部 Origin，不信任用户提供的转发身份头。

验收：真实证书校验、未登录拒绝、Secure/HttpOnly/SameSite cookie、登录 session 轮换、跨来源与缺 CSRF 拒绝、实际任务结果、退出失效、代理关闭内部路由，即使带正确内部 token 也不转发。保留正常回环 Worker 通信。仍不代表域名、ACME 续期、云网络或公网负载已验证。

源码基线：`after-backup-20261108`，329 文件，归档 SHA-256 `839e74f11497de08b56fbea99627c22665fff1b2097c6c00ab6f832961e69587`，位于 `.local/baselines/`，成员及逐文件摘要已核验。
