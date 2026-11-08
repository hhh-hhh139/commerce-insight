# 多表查询层对比

先读 [实施前约定](../../docs/research/002-multitable-query-plan.md)。本实验不调用模型；比较相同规范化数据和人工核定的查询意图，不评价自然语言生成 SQL 的准确率。

需要 Python 3.12。实验安装版本为 CPython 3.12.16、Wren 0.17.1、wren-core-py 0.8.2、DuckDB 1.5.0。完整依赖和哈希在 `requirements.lock`；这是实验环境，不是项目最终运行环境。

如果已有 Python 3.12，可在项目根目录创建隔离环境：

```sh
python3.12 -m venv .local/wren-venv
PIP_CONFIG_FILE=/dev/null .local/wren-venv/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple --cache-dir .local/pip-cache \
  --only-binary=:all: --require-hashes -r experiments/query-layer/requirements.lock
```

如果需要安装独立 Python，使用已安装的 uv 时先将其 Python 目录、缓存和链接目录指定到本项目 `.local/`，再执行 `uv --no-config python install 3.12.16 --no-bin`。本次实际使用的 uv 为 0.13.7；没有修改机器全局 Python、PATH 或用户配置。不要运行第三方全局安装脚本。

运行：

```sh
.local/wren-venv/bin/python -m unittest discover -s tests -v
.local/wren-venv/bin/python experiments/query-layer/compare.py \
  --output .local/probes/query-layer-comparison.json
```

脚本按相同规范化快照生成仅供本次实验使用的临时库，再顺序运行两个后端，避免同进程不同 DuckDB 连接配置相互冲突。Wren 使用显式 manifest、connection 和 `WrenConfig(strict_mode=True)`；不使用全局 profile、CLI 自动发现、数据库账号或 memory／embedding 功能。

- `cases.json`：15 个预先核定的金额／计数任务，包含跨期、累计退款、空区间、参数含 SQL 字符等情况。
- `mdl.json`：三表及一对多关系，同时声明关系聚合字段，验证语义层对关联的实际帮助。
- `compare.py`：记录受控 SQL、绑定参数、Wren 输入和规划后 SQL、结果、错误及环境。两个方案共用输入验证；Wren SDK 无绑定参数接口，实验用 SQLGlot 的字符串字面量编码输入参数。
- 两条故意错误的聚合查询作为反例，不计入正常任务通过率。边界实验仅操作生成的临时库与合成哨兵文件。

输出里必须分别查看 `summary`、`relationship_examples`、`negative_aggregation_examples`、`boundary_checks` 和 `database_unchanged`。脚本允许把候选框架失败完整写成报告，因此进程退出 0 **不代表所有比较项通过**。

耗时包含不同调用路径的准备成本：受控端为一次执行；Wren 端包含显式 dry-plan 和 query（query 自身再次规划）。仅用于记录环境现象，不能拿这些数字声称某方案快多少倍。生产性能需另外设计公平的负载实验。

限定：当前为合成数据和单进程本地环境；缺权限服务、持久化任务、超时终止、故障恢复、多用户并发与真实模型评测。workspace 参数只做快照范围核对，不充当身份认证。
