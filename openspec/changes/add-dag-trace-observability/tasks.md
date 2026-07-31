## 1. Span name 与属性 key 注册

- [x] 1.1 在 `backend/app/observability/span_names.py` 的 `SPAN_NAMES` 字典新增三个 key：`"dag.execute": "dag.execute · DAG执行"`、`"dag.wave": "dag.wave · 波次调度"`、`"dag.node": "dag.node · 节点执行"`
- [x] 1.2 在 `backend/app/observability/instrumentation.py` 新增属性 key 常量：`AGENTHUB_TASK_COUNT`、`AGENTHUB_WAVE_COUNT`、`AGENTHUB_WAVE_INDEX`、`AGENTHUB_WAVE_TASK_COUNT`、`AGENTHUB_READY_COUNT`、`AGENTHUB_SKIPPED_COUNT`、`AGENTHUB_NODE_STATUS`、`AGENTHUB_DEPENDS_ON`

## 2. dag_executor.py 埋点

- [x] 2.1 在 `execute_dag()` 函数体最外层包裹 `start_span("dag.execute", task_count=len(tasks), wave_count=len(waves), parent_run_id=ctx.parent_run_id, conversation_id=ctx.conversation_id)`，在函数退出时调用 `run_span_collector.record(ctx.parent_run_id, "dag.execute", task_count=..., wave_count=...)`
- [x] 2.2 在 `execute_dag()` 的 `for wave in waves` 循环内包裹 `start_span("dag.wave", wave_index=wave_idx, wave_task_count=len(wave), ready_count=len(ready), skipped_count=len(skipped))`，`wave_idx` 为 0-based 枚举计数器
- [x] 2.3 在 `_execute_node()` 函数体最外层包裹 `start_span("dag.node", task_id=task.id, child_agent_id=task.agent_id, dispatch_depth=ctx.dispatch_depth, dispatch_visibility=ctx.dispatch_visibility, depends_on=",".join(task.depends_on or []), node_status=...)`，在退出时调用 `run_span_collector.record(ctx.parent_run_id, "dag.node", task_id=..., node_status=..., depends_on=...)`
- [x] 2.4 在 `execute_dag()` 的 skipped 节点循环内，为每个 skipped 节点单独产生 `dag.node` span（`start_span("dag.node", task_id=t.id, node_status="skipped", error=reason, depends_on=",".join(t.depends_on or []))`）+ 对应 `run_span_collector.record()`
- [x] 2.5 在 `dag.execute` 和 `dag.node` 的 `run_span_collector.record()` 调用外层加 `if is_trace_enabled():` 守卫，`trace_enabled=False` 时跳过 record 避免无意义内存分配

## 3. 验证

- [x] 3.1 单元测试：`test_dag_executor` 新增测试用例，mock `spawn_subagent_loop`，断言 `dag.execute` / `dag.wave` / `dag.node` span 被创建且层级正确（parent-child 关系）
- [x] 3.2 单元测试：断言 skipped 节点产生 `dag.node` span 且 `node_status=skipped`、无子 `tool.dispatch`
- [x] 3.3 单元测试：断言 `run_span_collector.collect(run_id)` 包含 `dag.execute` 和各 `dag.node` 快照
- [x] 3.4 `ruff check backend/app/observability/ backend/app/services/dag_executor.py` 通过
- [x] 3.5 `pytest backend/tests/ -k dag` 通过
