# 单店主登录模式

这是本应用的独立 owner 账户，与 GitHub、模型 API Key 或公司账户无关。当前一个部署只管理一个工作空间。没有注册、邀请、找回邮箱、多人权限或多租户承诺。

## 本地运行

在项目根目录、自己的终端中执行：

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python scripts/prepare_owner.py
.local/agent-venv/bin/python scripts/web_demo.py --owner
```

准备脚本两次隐藏输入本应用密码（16–72 UTF-8 字节），调用 Spring Security BCrypt cost 12，运行目录只保存哈希，权限 0600。账户固定 `owner`。打开 `http://127.0.0.1:18080/login`，可以查询、导入公开/合成数据、核对报表和退出；默认无模型费用。没有为用户预设默认密码。

默认目录 `.local/owner-demo/offline/` 与旧免登录 Demo 分开。替换密码需 `prepare_owner.py --rotate` 并重启服务，重启使现有内存会话失效。更换端口用 `--port`，脚本不会停止或复用未知服务。原命令不加 `--owner` 时仍是严格回环的本地演示。

可选实时模式使用 `.local/owner-demo/live/`：先用 `prepare_owner.py --runtime .local/owner-demo/live` 设置独立应用密码，再运行 `web_demo.py --owner --live`，按此前约定在个人终端输入模型 Key。模型费用仍共用已有预算账本，价格日期不合要求时停止，不会因 owner 模式重置额度。

## 验证

```sh
.local/agent-venv/bin/python experiments/owner-access/verify.py .local/owner-access/latest.json
```

测试为每轮生成临时合成密码，不读取真实个人凭据，创建自己的随机回环端口与 H2 库；明文密码不写文件。检查匿名拒绝、会话 ID 更换、CSRF、任务执行、服务角色隔离、退出/重启失效、Host/Origin 和代理头、登录尝试窗口、缺失/错误配置拒绝启动。

本地 HTTP 验证和 HTTPS Secure-cookie 配置检查不等于公网 TLS 验收。实际 TLS、反向代理与干净环境运行在部署阶段单独验收。

## 部署约束

- 非回环监听必须选择 owner 模式、HTTPS Origin、Secure cookie 与 TLS 代理。`--origin` 必须精确对应浏览器 Origin（无路径、尾斜杠、查询或用户信息），不读取不可信的代理身份头。
- 页面使用 Spring Security 服务端会话、HttpOnly / SameSite=Strict cookie、默认 30 分钟空闲超时和 CSRF。登录/退出跳转是固定相对路径，不由请求传入跳转地址。
- `/api/` 与 `/internal/` 是独立 bearer 入口，owner 模式还要求回环来源与回环 Host；不要在代理中转发这些路径。网页 session 不具备 Worker 权限。
- 默认每进程全局 10 次登录 POST/分钟，有界状态、HTTP 429 与 Retry-After；用于单店主基本保护，重启重置，不是分布式防护或账户锁定系统。
- 当前会话存在内存，重启需重新登录；业务任务与报告持久化。默认仍是单 JVM/H2，需要后续部署文档中的资源、备份和恢复边界。

决策与备选见 [013](../../docs/research/013-owner-access.md)。不能把应用准备好部署写成已完成公网发布。
