# 个人仓库发布与干净 CI

日期：2026-11-08。当前个人仓库已创建，待发布源码；CI 未运行，不能把下面的操作说明当成通过记录。

## 为什么采用这个入口

目标是把审核后的完整目录发布到 `hhhhao139-ai/commerce-insight`，同时遵守公司电脑与个人项目的隔离约定。网页已验证登录归属和空仓库，但浏览器登录不代表本机 CLI 身份。

对比：网页逐文件上传不需要 CLI 凭据，但 [GitHub 一次最多 100 文件](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)，当前浏览器工具的文件输入没有目录选择能力；项目超过 350 文件。直接调用默认 `git push` 可能继承全局 credential helper、SSH 或 URL rewrite，违背边界。选择准备固定提交，用户在终端提供仅授权此仓库的短期 PAT，程序核对 `/user`、仓库与权限再推送，回读 SHA 后记录成功。没有自建 Git 协议，也不添加长期部署凭据。

依据：[GitHub fine-grained PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) 支持限定 owner、仓库与权限；[Git 认证](https://git-scm.com/docs/gitcredentials) 支持独立 askpass；[Git 配置](https://git-scm.com/docs/git-config) 支持禁用 helper 和主动认证。核对了 [Git 2.50.1 HTTP 实现](https://raw.githubusercontent.com/git/git/v2.50.1/http.c)：`http.proactiveAuth=basic` 在请求前获得用户名/密码并设置 libcurl；[libcurl 文档](https://curl.se/libcurl/c/CURLOPT_NETRC.html) 说明显式用户名/密码优先于 netrc。此脚本限定已检查的本机 Git 版本，版本变化需复核。以上官方资料于当日访问。

## 用户终端的一次身份操作

1. 在个人 GitHub 的 Settings → Developer settings → Personal access tokens → Fine-grained tokens 创建短期 token。Resource owner 选 `hhhhao139-ai`，Only select repositories 仅选 `commerce-insight`；Repository permissions 只增加 **Contents: Read and write** 与 **Workflows: Read and write**（仓库包含 CI 文件），Metadata 保持自动权限；可选 1 天有效期。不要选择公司组织或所有仓库。
2. 在自己的终端执行下面的命令，按提示输入 PAT；不会回显，不要把 PAT 放进命令参数或聊天。程序会在发送源码前校验账号与仓库；token 本身只能发送给 GitHub 的认证/API/HTTPS Git 端点。

```sh
/Users/haohao4/Downloads/multi-agent.zip/commerce-insight/.local/agent-venv/bin/python /Users/haohao4/Downloads/multi-agent.zip/commerce-insight/scripts/publish_personal.py
```

3. 成功时 `.local/personal-release/published.json` 保存仓库名和 commit，没有 token。随后可在个人 GitHub 撤销此短期 token。认证或网络失败不会记录“已发布”，不要将密码/PAT 或包含凭据的终端内容发到聊天。

实际推送的 commit 与 tree 由 `.local/personal-release/approved.json` 指定，即使本地随后有其他修改也不把它们自动加入；不执行 force push，不覆盖远程分叉历史。配置仅有新项目原始 core 项时才允许发布。公网 GitHub 连通性与真实授权仍需这一步实际验证，离线身份测试不能代替它们。

## 随源码交付的 CI

`.github/workflows/verify.yml` 使用官方固定 commit 的 Actions，权限为 `contents: read`，没有个人/公司 secret。仅本个人仓库 owner 的 GitHub-hosted Ubuntu runner 可运行部署验收器。流程解析并记录镜像 digest、构建镜像、执行离线契约、启动全新 PostgreSQL/应用/Caddy 卷，验证 TLS、会话、CSRF、任务与重启持久化，最后保存失败或成功的原始 artifact 并回收本次资源。没有模型 API 调用。

Compose 的 CI 覆盖用 `!override` 将端口限定为 runner 回环，依据 [Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/) 要求 Compose 2.24.4 及以上。不能省略该限制而把生产端口一起开放。当前未得到实际 Linux 镜像 digest，不捏造部署成功或镜像锁定记录。
