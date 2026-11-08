# 稳定性与资源控制验证

在项目根目录执行，使用项目内 JDK/Maven/Python；不调用模型。测试会启动并关闭自己的随机回环端口服务，只写 `.local/reliability/`。

```sh
.local/agent-venv/bin/python scripts/java_local.py build
.local/agent-venv/bin/python experiments/reliability/verify.py .local/reliability/latest.json
```

12 项专项检查包含 2 项 Python 百分比边界、实际 Java/Worker 销售与脚本 Agent 发布、并发准入/幂等、并发租约、持续心跳与重启截止、无 Worker 到期、保留上限、角色与空间隔离、输出增长中止、导入事务回滚、旧表结构迁移。测试容量为总量 5、活跃 3、有效租约 1；截止时间使用 2 秒或 15 秒。这不是默认容量的性能压测。

## 使用与运维含义

默认限额在 Java `ResourceControls` 中：总任务 2000、活跃 32、有效租约 4、入队后 900000ms 截止。可由 `commerce.limits.total/active/running/lifetime-ms` 显式配置，启动时验证范围。任务包括分析和导入检查；历史仍计入总量。幂等重复不占新额度。有效租约数不是短暂失联旧进程的物理数；共享预算和旧租约结果拒绝继续生效。

超限返回 HTTP 429 与 `task_active_quota_exceeded` 或 `task_retention_quota_exceeded`。截止到期为 `FAILED / task_deadline_exceeded`，附一条 `DEADLINE_EXCEEDED` 事件。`expires_at` 是入队时固定的毫秒时间戳，心跳、重试、配置变化与重启不会延长已有任务。升级前没有该列的旧任务按 `created_at + 15 分钟` 回填；旧终态不变，旧未完成超期任务将结束。

用户角色可读取 `GET /api/workspaces/demo-shop/system`；本地工作台同源端点是 `GET /demo/api/system`，沿用已有鉴权。返回状态计数、最老排队等待毫秒、最近 Worker 拉取时间与限额；没有原始数据、问题、Key 或 lease token。最近拉取仅证明 Worker 最近访问过，不保证模型或所有下游健康。

Worker 对 stdout/stderr 做轮询大小检查，超限中止并报告 `executor_output_limit`。stdout 普通任务上限 60000 字节、导入 1800000 字节，stderr 65536 字节；检查间隔约 30ms，允许瞬时超量。不是磁盘硬配额或不可信代码沙箱，生产资源约束还应由部署层提供。

历史不自动删除，归档数据仍可用于旧报告。达到保留上限时不能通过重启清空额度；先导出、备份，再执行明确维护流程。删除数据、保留期限选择、数据库迁移与恢复将在部署交付中说明，本模块不删除任何已有个人历史。

## 证据

[修复前 8 项失败](../../docs/probes/reliability-before-2026-11-06.json)是对新约束的反例，不代表八次线上事故。[最终 12/12](../../docs/probes/reliability-release-2026-11-06.json)加入了导入交叉验证、真实发布和旧表结构迁移；不以样本数变化计算提升率。测试代码、研究与阶段记录共同说明预期、实现与限制。
