# 接入指南

Aeval 与你的 Agent 系统之间只依赖少量小协议。**必选的只有一个**：`AgentRunner`；其余（TraceProvider / Storage / EnvironmentManager / Grader）都有默认实现，按需替换。

## 1. AgentRunner（必选）

框架通过 `run(task)` 与 Agent 交互，不需要知道 Agent 内部实现：

```python
from agent_eval.core.types import EvalTask

class MyAgentRunner:
    async def run(self, task: EvalTask) -> tuple[str, list[dict], dict]:
        # 1. 按 task.env 准备环境（种子文件等）
        # 2. 把 task.prompt 发给你的 Agent，等待完成
        # 3. 收集结果
        return trace_id, transcript, outcome
        #      ^str     ^对话记录     ^环境最终状态
```

约定：

- **transcript**: `[{"role": "user"|"assistant"|..., "content": ...}, ...]` — `code_based` 的 `target: transcript` 在此搜索
- **outcome**: 任意 JSON dict（如 `{"files": {...}}`）— `target: outcome` 在此搜索（深序列化后匹配）
- **trace_id**: OTel trace id；没有 trace 体系就传 `""`
- **瞬时故障**：网络超时/抖动请显式抛 `TransientError`（`agent_eval.core.contract`）— 框架做指数退避重试（默认最多 2 次）；其余异常直接判该 trial 失败，不重试

### HTTP Agent 适配

Agent 以 HTTP 服务暴露时，参考 `examples/achat/http_agent_runner.py`：提交 prompt → 轮询至完成 → 组装三元组。AChat 仓库的 `AChatAgentRunner` 是完整生产实现（含 workspace 沙箱与事件总线完成检测）。

## 2. Runner 注册与选择

`eval-suite run --runner <name>`（或环境变量 `AEVAL_RUNNER`）按名称解析 AgentRunner：

- `mock` — 内置 MockAgentRunner（默认）
- 自定义 — 在你的包里声明 entry-point，name → **零参工厂**：

```toml
[project.entry-points."agent_eval.runners"]
my-agent = "my_pkg.runner:create_runner"
```

```python
def create_runner() -> MyAgentRunner:
    return MyAgentRunner(api_base=os.environ["MY_AGENT_URL"])
```

在纯 Python 侧使用时则完全不需要注册——直接把实例传给 `EvalRunner`。

## 3. TraceProvider（可选）

默认 `MockTraceProvider`（返回空 span）。接 Phoenix：

```python
from agent_eval.trace import PhoenixProvider

runner = EvalRunner(..., trace_provider=PhoenixProvider(endpoint="http://localhost:6006"))
```

span 数据用于：过程指标提取（`tracked_metrics`）、`tool_calls` / `step_level` / `transcript` 评分器、Trial 下钻与 Phoenix 外链。需要 `pip install arize-phoenix`（懒加载，未装不报错，除非真的调用）。

## 4. Storage（可选）

默认 `MemoryStorage`（进程内，不落盘）。CLI 的 `run` 默认使用 SQLite：

```python
from agent_eval.storage.sqlite import SqliteStorage

storage = SqliteStorage("./aeval.db")
await storage.initialize()
runner = EvalRunner(..., storage=storage)
```

`Storage` 协议核心方法：`save_run / get_run / list_runs / delete_run / save_suite / get_suite / list_suites`。PostgreSQL 后端在路线图上。

## 5. EnvironmentManager（可选）

默认 `NoOpEnvironment`（无操作）。要做环境隔离与泄漏检测时实现：

```python
class MyEnvironment:
    async def setup(self, task) -> None: ...          # 每 trial 开始（种子文件等）
    async def teardown(self, task) -> None: ...       # 每 trial 结束
    async def snapshot(self) -> dict: ...             # 环境基线快照
    async def verify_clean(self, baseline) -> dict:   # trial 后比对
        return {"clean": True, "differences": []}
    async def restore(self, baseline) -> None: ...    # 泄漏时恢复基线
```

`verify_clean` 报告不干净时：框架自动 `restore` 并记录告警，**不**判 trial 失败（失败只由评分决定）。

## 6. 自定义 Grader（可选）

```python
from agent_eval.core.types import GraderResult, GraderType

class CodeStyleGrader:
    name = "code_style"          # suite 里 name 字段引用这个名字

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        ...
        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=0.9, passed=True, explanation="style ok",
        )

runner = EvalRunner(..., graders=[CodeStyleGrader()])  # 与内置合并，同名覆盖内置
```

评分器之间可以声明依赖（拓扑排序执行）：`GraderConfig(dependencies=["code_based"])` — 依赖未通过时依赖方自动跳过。

## 7. LLM 依赖的注入

`model_based`（LLM-as-Judge）与 `metric` 类评分器需要 LLM 回调：

```python
async def my_llm(system: str, user: str) -> str: ...

runner = EvalRunner(..., llm_fn=my_llm)   # 注入未自行配置的 judge/指标
```

也可以走指标注册表：`metrics_registry={...}`（见 `agent_eval.metrics`）。缺配置时框架返回**带原因的 0 分结果**，不会 crash 整个 run。

## 8. 挂载 API（可选）

把评测 API 挂进你已有的 FastAPI 应用：

```python
from agent_eval.api.app import create_app

app.mount("/api/eval", create_app(runner=my_runner))   # 路径随你
```

或独立部署（`/v1` 前缀 + `X-Aeval-Version` 头 + `/v1/meta`）：

```python
from agent_eval.api.standalone import create_standalone_app

uvicorn.run(create_standalone_app(runner=my_runner), host="127.0.0.1", port=8000)
# 或直接: eval-suite serve --port 8000
```

API 兼容承诺：同一大版本内 URL 与响应结构向后兼容；破坏性变更升 `/v2` 并保留并行期。
