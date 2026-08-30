# CLI 参考

`eval-suite` 随 `[cli]` extra 提供（`pip install "agent-eval[cli]"`）。查看帮助：

```bash
eval-suite --help
eval-suite <command> --help
```

## run — 执行套件

```bash
eval-suite run <suite.yaml> [选项]
```

| 选项 | 默认 | 说明 |
|------|------|------|
| `--trials N` | 用 suite 配置 | 覆盖每个任务的 trial 数 |
| `--concurrency N` | 1（串行） | trial 并发数 |
| `--runner NAME` | `mock` | AgentRunner；也读环境变量 `AEVAL_RUNNER` |
| `--db PATH` | `./aeval.db` | 结果 SQLite 路径；也读 `AEVAL_DB` |

行为：加载校验 suite → 解析 runner → 执行 → 打印 §11.2 形态的汇总（pass@k / pass^k / 平均分 / 失败任务清单）→ **存在失败任务时退出码 1**；suite 加载失败退出码 1；runner 未知退出码 2。

自定义 runner 通过 entry-point 注册（见接入指南 §2），例如：

```toml
[project.entry-points."agent_eval.runners"]
my-agent = "my_pkg.runner:create_runner"
```

## validate — 校验套件

```bash
eval-suite validate <suite.yaml>
```

只做加载校验不执行。合法输出 `VALID: <name> vX — N task(s)...`；非法输出 `INVALID:` + 具体校验错误（如 `Duplicate task IDs`），退出码 1。适合放进 CI 在运行前挡格式错误。

## list — 列出 runs / suites

```bash
eval-suite list runs [--db PATH] [--limit N]
eval-suite list suites [--db PATH]
```

- `runs`：run_id / suite / 状态 / 开始时间（默认 50 条）
- `suites`：曾执行并落库的套件（名称 / 版本 / 任务数 / 描述）

## show — 运行详情

```bash
eval-suite show <run_id> [--task TASK_ID] [--db PATH]
```

输出 run 元信息、pass@k / pass^k / 平均分、逐任务表格（trials、pass@1、平均分、PASS/FAIL、pending 数）。

- `--task <id>`：下钻单任务 —— 逐 trial 的成败 / 得分 / 耗时 / 错误，以及每个 grader 的评分明细（分数、通过、解释）
- run 不存在 → 退出码 1；task 不在 run 内 → 退出码 1

## compare — A/B 对比

```bash
eval-suite compare <run_a> <run_b> [--db PATH]
```

输出（与 REST API `POST /compare` 同一语义）：

- 全局指标 delta 表：`Pass@k` / `Pass^k` / `Avg Score`（A 值、B 值、差值）
- `Regressions:` 退化任务清单（平均分下降 > 0.1）
- `Improvements:` 提升任务清单（上升 > 0.1）

## serve — 独立 API 服务

```bash
eval-suite serve [--host 127.0.0.1] [--port 8000]
```

以 uvicorn 启动独立 API（`create_standalone_app()`）：

- 全部评测路由挂 **`/v1`** 前缀（`/v1/suites`、`/v1/runs`、`/v1/graders`、`/v1/compare`、`/v1/datasets`、`/v1/metrics`、`/v1/health`）
- 每个响应带 `X-Aeval-Version` 头
- `GET /v1/meta` 返回版本与能力清单
- **默认仅监听本机回环地址**（对外暴露请显式 `--host 0.0.0.0` 并自行考虑访问控制）

注意：`serve` 不注入 AgentRunner，run 类操作返回 503 —— 需要真实执行时用 Python 侧 `create_standalone_app(runner=...)` 或寄宿挂载。

## 环境变量汇总

| 变量 | 作用 |
|------|------|
| `AEVAL_RUNNER` | `run` 的默认 runner 名 |
| `AEVAL_DB` | 结果 SQLite 默认路径 |
