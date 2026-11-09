# 个人仓库发布与干净 CI

更新：2026-11-09。用户换用个人账号 `hhh-hhh139`，本人终端发布 `8769463` 后已核对远程提交；三个局部修复在同一个人浏览器会话完成。应用提交 `8855db6` 的[干净 Linux CI](https://github.com/hhh-hhh139/commerce-insight/actions/runs/42557209900)通过，过程和局限见[阶段 5f](baselines/phase-05f-2026-11-09.md)。旧账号与旧发布候选不再适用。

## 为什么采用这个入口

目标是把审核后的完整目录发布到 `hhh-hhh139/commerce-insight`，同时遵守公司电脑与个人项目的隔离约定。网页已验证登录归属和空仓库，但浏览器登录不代表本机 CLI 身份。

对比：网页逐文件上传不需要 CLI 凭据，但 [GitHub 一次最多 100 文件](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository)，此前检查的网页文件输入没有目录选择能力；项目超过 350 文件。直接调用默认 `git push` 可能继承全局 credential helper、SSH 或 URL rewrite，违背边界。选择准备固定提交，用户在终端提供仅授权此仓库的短期 PAT，程序核对 `/user`、仓库与权限再推送，回读 SHA 后记录成功。没有自建 Git 协议，也不添加长期部署凭据。

依据：[GitHub fine-grained PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) 支持限定 owner、仓库与权限；[Git 认证](https://git-scm.com/docs/gitcredentials) 支持独立 askpass；[Git 配置](https://git-scm.com/docs/git-config) 支持禁用 helper 和主动认证。核对了 [Git 2.50.1 HTTP 实现](https://raw.githubusercontent.com/git/git/v2.50.1/http.c)：`http.proactiveAuth=basic` 在请求前获得用户名/密码并设置 libcurl；[libcurl 文档](https://curl.se/libcurl/c/CURLOPT_NETRC.html) 说明显式用户名/密码优先于 netrc。此脚本限定已检查的本机 Git 版本，版本变化需复核。以上官方资料于当日访问。

## 用户终端的一次身份操作

1. 在个人 GitHub 的 Settings → Developer settings → Personal access tokens → Fine-grained tokens 创建短期 token。Resource owner 选 `hhh-hhh139`，Only select repositories 仅选 `commerce-insight`；Repository permissions 只增加 **Contents: Read and write** 与 **Workflows: Write**（仓库包含 CI 文件），Metadata 保持自动只读权限；有效期选 1 天。不要选择公司组织或所有仓库。也可使用官方支持的[预填入口](https://github.com/settings/personal-access-tokens/new?name=commerce-insight-publish&target_name=hhh-hhh139&expires_in=1&contents=write&workflows=write)，仍须选择唯一仓库并由本人生成凭据。
2. 在自己的终端执行下面的命令，按提示输入 PAT；不会回显，不要把 PAT 放进命令参数或聊天。程序会在发送源码前校验账号与仓库；token 本身只能发送给 GitHub 的认证/API/HTTPS Git 端点。

```sh
/Users/haohao4/Downloads/multi-agent.zip/commerce-insight/.local/agent-venv/bin/python /Users/haohao4/Downloads/multi-agent.zip/commerce-insight/scripts/publish_personal.py
```

3. 成功时 `.local/personal-release/published.json` 保存仓库名和 commit，没有 token。随后可在个人 GitHub 撤销此短期 token。认证或网络失败不会记录“已发布”，不要将密码/PAT 或包含凭据的终端内容发到聊天。

实际推送的 commit 与 tree 由 `.local/personal-release/approved.json` 指定，即使本地随后有其他修改也不把它们自动加入；不执行 force push，不覆盖远程分叉历史。配置仅有新项目原始 core 项时才允许发布。公网 GitHub 连通性与真实授权仍需这一步实际验证，离线身份测试不能代替它们。

## 随源码交付的 CI

`.github/workflows/verify.yml` 使用官方固定 commit 的 Actions，权限为 `contents: read`，没有个人/公司 secret。仅本个人仓库 owner 的 GitHub-hosted Ubuntu runner 可运行部署验收器。流程解析并记录镜像 digest、构建镜像、执行离线契约、启动全新 PostgreSQL/应用/Caddy 卷，验证 TLS、会话、CSRF、任务与重启持久化，最后保存失败或成功的原始 artifact 并回收本次资源。没有模型 API 调用。

Compose 的 CI 覆盖用 `!override` 将端口限定为 runner 回环，依据 [Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/) 要求 Compose 2.24.4 及以上。不能省略该限制而把生产端口一起开放。实际基础镜像 digest、四轮结果与 artifact SHA 已保存在[公开证据](probes/clean-linux-2026-11-09.json)；默认精确版本 tag 不等于永久锁定所有构建输入。

## 新账号切换的依据与验证（2026-11-09）

浏览器设置页明确显示 `hhh-hhh139` 是个人账户，新仓库页面确认其所有权。原代码把新用户名拒绝为 `personal_account_mismatch_or_authentication_failed`，这是旧白名单的预期行为，不是业务缺陷；复现使用 Mock HTTP，无凭据或网络请求。修改前使用本地提交 `dd28300` 保留完整源码，旧发布审核记录另存 `.local/personal-release/retired-hhhhao139-ai-20261109/`。

比较删除身份限制、同时允许两个账号、仅替换为新目标：选择第三种，保持唯一明确的发布目的地。依据本日复核的 GitHub 官方 [authenticated user](https://docs.github.com/en/rest/users/users#get-the-authenticated-user) 和 [fine-grained PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) 文档，认证身份必须由实际 token 校验，token 可限定 owner、仓库和权限；浏览器身份只能证明当前网页会话。旧的目标审核记录必须在读取凭据前失效，重新审核最新候选后才能生成新记录。

发布脚本、askpass、CI workflow 与 CI Python 入口已同步。CI 从 owner 限制收紧到精确仓库 `hhh-hhh139/commerce-insight`。专项 **7/7** 通过（unittest 0.532 秒）：包括旧账号、旧审核记录、旧凭据提示、同账号的其他仓库在网络/文件副作用之前被拒绝，以及现有凭据环境隔离。见[测试记录](probes/personal-account-switch-2026-11-09.json)。这些结果不代表实际 token、源码推送或 Linux CI 已通过。
