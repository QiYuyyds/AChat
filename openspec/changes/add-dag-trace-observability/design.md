## Context

DAG 执行器（`backend/app/services/dag_executor.py`）在 Orchestrator `dispatch_plan` 工具调用时负责：拓扑排序（`topological_waves`）、波次并行调度（`asyncio.gather`）、上游失败级联跳过。该模块有完整的 event 流（`DispatchStartEvent` / `DispatchEndEvent` / `DispatchPlanEvent` / `PlanStepUpdateEvent`），但**零 OTel span 埋点**。

现有 trace 覆盖：

```
agent.run (Orchestrator)
  └─ tool.call (dispatch_plan)         ← tool registry 自动包裹
       └─ ← 虚空：execute_dag 无 span
            ├─ tool.dispatch (child A)  ← spawn_subagent_loop 内有
            │    └─ agent.run (Child A)
            ├─ tool.dispatch (child B)
            │    └─ agent.run (Child B)
            └─ ...（扁平，无波次/依赖/跳过信息）
```

`RunSpanCollector`（在线 eval 用）仅在 `registry.py`（`tool.call`）、`custom_adapter.py`（`llm.generate`）、`agent_runner.py`（`agent.finalize`）三处 record，DAG 执行信息对 eval 规则完全不可见。

## Goals

- Phoenix trace 树能直观反映 DAG 拓扑结构：波次分组、节点依赖、跳过原因
- `RunSpanCollector` 记录 DAG span，使在线 eval 规则可感知波次/节点状态
- 零业务逻辑改动，纯埋点包裹

## Non-Goals

- 不修改 `dag_executor.py` 的调度逻辑（拓扑排序、波次分组、并行执行）
- 不修改 `task_dispatch` 工具（单任务派发无 DAG 结构，现有 `tool.call` → `tool.dispatch` 已够用）
- 不新增 span exporter 或修改 Phoenix 发送链路
- 不修改前端监控页（瀑布流组件已支持任意嵌套 span）

## Decisions

### D1: 三层 span 层级 — dag.execute → dag.wave → dag.node

```
dag.execute (task_count=5, wave_count=3)
  ├─ dag.wave (index=0, task_count=2)
  │    ├─ dag.node (task_id="A", depends_on=[]) → tool.dispatch → agent.run
  │    └─ dag.node (task_id="B", depends_on=[]) → tool.dispatch → agent.run
  ├─ dag.wave (index=1, task_count=1)
  │    └─ dag.node (task_id="C", depends_on=["A","B"]) → tool.dispatch → agent.run
  └─ dag.wave (index=2, task_count=2)
       ├─ dag.node (task_id="D", depends_on=["C"]) → tool.dispatch → agent.run
       └─ dag.node (task_id="E", depends_on=["C"], node_status="skipped")
```

**为什么三层而不是两层**（execute + node，无 wave）：波次是 DAG 调度的核心语义——同一波内并行、波间串行。省去 `dag.wave` 会导致 Phoenix 里 node 扁平排列，无法直观看出并行/串行边界。

**为什么不让 `dag.execute` 直接包裹所有 node**（无 wave 中间层）：`asyncio.gather` 创建的 Task 各自 copy context，`dag.wave` span 必须在 gather 之前 open、在 gather 完成后 close，才能正确成为该波所有 node 的 parent。省掉它会导致 node 的 parent 变成 `dag.execute`，丢失波次边界。

### D2: asyncio.gather 的 context 传播 — 无需手动处理

Python 3.11+ 的 `asyncio.Task.__init__` 会 copy 当前 context（含 OTel contextvar）。`dag.wave` span 在主协程 open 后，`asyncio.gather(*coros)` 内每个 `_execute_node` 协程自动继承 `dag.wave` 作为 parent span。无需手动传递 context 或使用 `contextvars.copy_context()`。

验证方式：`dag.node` span 的 `parent_span_id` 应指向 `dag.wave` span 的 `span_id`。

### D3: skipped 节点也产生 dag.node span

上游失败导致下游跳过时，`_execute_node` 不会被调用（跳过逻辑在 `execute_dag` 的 wave 循环里）。但 skipped 节点应在 `dag.wave` 下产生一个 `dag.node` span，属性 `node_status=skipped` + `error=上游原因`，无子 `tool.dispatch`。

**为什么**：Phoenix 里看到完整的 DAG 拓扑（包括被跳过的节点），才能理解为什么某些分支没有执行。如果 skipped 节点不出现在 trace 里，用户会误以为任务从未被调度。

### D4: dag.execute 包裹在 tool.call 内部

`dispatch_plan` 工具的 `_handler` 函数已被 `tools/registry.py` 的 `tool.call` span 包裹。`execute_dag()` 在 `_handler` 内部被调用，所以 `dag.execute` span 自动成为 `tool.call` 的子 span。不需要修改 `dispatch_plan.py` 的 handler 代码——span 包裹在 `execute_dag` 函数内部。

### D5: RunSpanCollector 在 dag.execute 和 dag.node 退出时 record

`run_span_collector.record(run_id, span_name, **attrs)` 是手动调用，不会自动跟随 `start_span`。需要在 `dag.execute` 和 `dag.node` 的 span 退出点显式调用 `record()`。

**为什么 `dag.wave` 不 record**：wave 是结构化分组，eval 规则不需要单独检查波次——它们可以通过 node 的 `wave_index` 属性推导波次信息。减少 record 调用点，降低开销。

**为什么需要 `run_id`**：`RunSpanCollector` 按 `run_id` 隔离，eval hook 在 run 结束时 collect。DAG 执行的 `run_id` 是 Orchestrator 的 run_id（`ctx.parent_run_id`），需要从 `DagExecContext` 获取并传递到 record 调用。

### D6: 属性 key 命名

新增常量遵循现有 `agenthub.` 前缀约定：

| 常量 | Key | 用在 |
|---|---|---|
| `AGENTHUB_TASK_COUNT` | `agenthub.task_count` | dag.execute |
| `AGENTHUB_WAVE_COUNT` | `agenthub.wave_count` | dag.execute |
| `AGENTHUB_WAVE_INDEX` | `agenthub.wave_index` | dag.wave |
| `AGENTHUB_WAVE_TASK_COUNT` | `agenthub.wave_task_count` | dag.wave |
| `AGENTHUB_READY_COUNT` | `agenthub.ready_count` | dag.wave |
| `AGENTHUB_SKIPPED_COUNT` | `agenthub.skipped_count` | dag.wave |
| `AGENTHUB_NODE_STATUS` | `agenthub.node_status` | dag.node |
| `AGENTHUB_DEPENDS_ON` | `agenthub.depends_on` | dag.node |

已有可复用常量：`AGENTHUB_TASK_ID` / `AGENTHUB_CHILD_AGENT_ID` / `AGENTHUB_DISPATCH_DEPTH` / `AGENTHUB_DISPATCH_VISIBILITY` / `AGENTHUB_PARENT_RUN_ID` / `AGENTHUB_CONVERSATION_ID` / `AGENTHUB_ERROR`。

## Risks / Trade-offs

- **[span 数量膨胀]** 一次 5 任务 3 波 DAG 会从当前的 5 个 `tool.dispatch` span 增加到 1+3+5=9 个额外 span。→ 影响可控，BatchSpanProcessor 异步批量发送，不阻塞主链路。Phoenix span 总量增幅 < 20%。
- **[RunSpanCollector 内存]** 每个 DAG span 在 collector 里存一个 dict 快照，run 结束后 clear。→ 单次 run 额外 ~9 个 dict，可忽略。
- **[asyncio context 边界]** 极端情况下（如 `asyncio.gather` 内协程创建子 Task），context 可能不按预期传播。→ 仅在 `_execute_node` 内 open `dag.node` span，不跨 Task 边界，context 链不断裂。
