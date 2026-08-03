## Why

DAG 执行器（`dag_executor.py`）是 Orchestrator `dispatch_plan` 工具的核心调度引擎，负责拓扑排序、波次并行调度、上游失败级联跳过。但该模块**零 OTel 埋点**——在 Phoenix trace 树中，所有子任务派发扁平排列在 `tool.call(dispatch_plan)` 之下，无法看出波次分组、依赖关系、跳过原因。同时 `RunSpanCollector`（在线 eval 用）也不记录 DAG 执行 span，导致评测规则无法感知 DAG 拓扑。

现有 `add-trace-observability` change 覆盖了 `agent.run` / `adapter.stream` / `rag.search` / `tool.call` / `tool.dispatch` / `memory.recall` 等埋点，但完全没有提及 `dag_executor.py`——该模块是在 observability 设计之后加入的。

## What Changes

- 在 `span_names.py` 注册三个新 span key：`dag.execute` / `dag.wave` / `dag.node`，附带中英双语名
- 在 `dag_executor.py` 的 `execute_dag()` 外层包裹 `dag.execute` span，记录 `task_count` / `wave_count` / `parent_run_id` / `conversation_id`
- 在 `execute_dag()` 的每个 wave 循环内包裹 `dag.wave` span，记录 `wave_index` / `wave_task_count` / `ready_count` / `skipped_count`
- 在 `_execute_node()` 外层包裹 `dag.node` span，记录 `task_id` / `child_agent_id` / `depends_on` / `dispatch_depth` / `dispatch_visibility` / `node_status`
- skipped 节点也产生 `dag.node` span（无子 `tool.dispatch`，但 `node_status=skipped` + `error=上游原因`）
- 在 `instrumentation.py` 新增 DAG 相关属性 key 常量：`AGENTHUB_TASK_COUNT` / `AGENTHUB_WAVE_COUNT` / `AGENTHUB_WAVE_INDEX` / `AGENTHUB_WAVE_TASK_COUNT` / `AGENTHUB_READY_COUNT` / `AGENTHUB_SKIPPED_COUNT` / `AGENTHUB_NODE_STATUS` / `AGENTHUB_DEPENDS_ON`
- 在 `dag_executor.py` 的 span 退出点调用 `run_span_collector.record()`，使在线 eval 规则可感知 DAG 拓扑

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `trace-observability`: 新增 DAG 执行层 span 采集要求（`dag.execute` / `dag.wave` / `dag.node`）及 RunSpanCollector 对 DAG span 的记录要求。该 capability 由 pending change `add-trace-observability` 引入，本 change 在其基础上追加 DAG 覆盖。

## Impact

- 修改文件（仅埋点包裹，不改业务逻辑）：
  - `backend/app/observability/span_names.py`：注册 3 个新 span key
  - `backend/app/observability/instrumentation.py`：新增 DAG 属性 key 常量
  - `backend/app/services/dag_executor.py`：`execute_dag()` / `_execute_node()` 加 `start_span` + `run_span_collector.record()`
- 不新增依赖
- 不影响现有代码语义：所有 span 包裹为 `with start_span(...)` 上下文管理器，`trace_enabled=False` 时自动 no-op
- 不修改 DB schema
- asyncio.gather 并行任务的 OTel context 传播基于 `contextvars`，Python 3.11+ `asyncio.Task` 自动 copy context，`dag.wave` → `dag.node` 父子关系自动正确
