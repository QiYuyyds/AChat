## ADDED Requirements

### Requirement: DAG 执行层 span 采集

系统 SHALL 对 `dag_executor.py` 的 DAG 执行过程产生三层嵌套 span，反映拓扑排序的波次结构与节点级联跳过。

- `execute_dag()` SHALL 产生 `dag.execute` span，属性含 `agenthub.task_count`(int)、`agenthub.wave_count`(int)、`agenthub.parent_run_id`、`agenthub.conversation_id`
- `execute_dag()` 的每个 wave 循环 SHALL 产生 `dag.wave` span（`dag.execute` 的子 span），属性含 `agenthub.wave_index`(int, 0-based)、`agenthub.wave_task_count`(int)、`agenthub.ready_count`(int)、`agenthub.skipped_count`(int)
- `_execute_node()` SHALL 产生 `dag.node` span（所属 `dag.wave` 的子 span），属性含 `agenthub.task_id`、`agenthub.child_agent_id`、`agenthub.dispatch_depth`、`agenthub.dispatch_visibility`、`agenthub.depends_on`(comma-separated string)、`agenthub.node_status`(complete|failed|aborted|skipped)
- skipped 节点 SHALL 产生 `dag.node` span，属性 `agenthub.node_status=skipped` + `agenthub.error` 设为上游失败原因，无子 `tool.dispatch` span
- `dag.node` span SHALL 通过 OTel parent-child 上下文自动嵌套在所属 `dag.wave` span 之下（asyncio.Task copy context 传播）
- 所有 DAG span SHALL 在 `trace_enabled=False` 时变为 no-op，不影响调度逻辑

#### Scenario: DAG span 树嵌套

- **WHEN** Orchestrator 调用 `dispatch_plan` 派发 5 个任务（3 波：2+1+2），其中最后一波有 1 个节点因上游失败被 skip
- **THEN** Phoenix trace 树 SHALL 包含 `tool.call(dispatch_plan)` > `dag.execute` > 3 个 `dag.wave` span
- **AND** 每个 `dag.wave` 下 SHALL 有对应数量的 `dag.node` 子 span
- **AND** 被跳过节点的 `dag.node` span SHALL 有 `agenthub.node_status=skipped` 且无子 `tool.dispatch` span
- **AND** 每个正常执行节点的 `dag.node` 下 SHALL 有 `tool.dispatch` > `agent.run` 子 span

#### Scenario: 波次并行可见性

- **WHEN** Wave 0 包含 2 个无依赖任务 A 和 B
- **THEN** `dag.wave(index=0)` span 的 `agenthub.wave_task_count` SHALL 为 2
- **AND** `dag.node(task_id="A")` 和 `dag.node(task_id="B")` SHALL 为 `dag.wave(index=0)` 的兄弟子 span
- **AND** 两者的 `agenthub.depends_on` SHALL 均为空字符串

#### Scenario: 依赖关系记录

- **WHEN** 任务 C 依赖任务 A 和 B（`depends_on=["A","B"]`）
- **THEN** `dag.node(task_id="C")` span 的 `agenthub.depends_on` SHALL 为 `"A,B"`
- **AND** 该 span 的 `agenthub.wave_index`（从父 `dag.wave` 继承）SHALL 大于 A 和 B 的 wave index

#### Scenario: 采集关闭

- **WHEN** `trace_enabled=False`
- **THEN** `execute_dag()` 和 `_execute_node()` SHALL 正常执行，不产生任何 span
- **AND** DAG 调度逻辑不受影响

### Requirement: RunSpanCollector 记录 DAG span

系统 SHALL 在 `dag.execute` 和 `dag.node` span 退出时调用 `run_span_collector.record()`，使在线 eval 规则可感知 DAG 拓扑与节点状态。

- `execute_dag()` 退出时 SHALL 调用 `run_span_collector.record(run_id, "dag.execute", task_count=..., wave_count=...)`
- 每个 `_execute_node()` 退出时（含 skipped 节点）SHALL 调用 `run_span_collector.record(run_id, "dag.node", task_id=..., node_status=..., depends_on=...)`
- `dag.wave` span SHALL NOT 调用 `run_span_collector.record()`（波次信息可通过 node 的属性推导，减少开销）
- `run_id` 参数 SHALL 取自 `DagExecContext.parent_run_id`（Orchestrator 的 run ID）
- `trace_enabled=False` 时 SHALL NOT 调用 `record()`（避免无意义内存分配）

#### Scenario: 在线 eval 可感知 DAG 拓扑

- **WHEN** 一次 Orchestrator run 完成，`_run_online_eval_hook` 调用 `run_span_collector.collect(run_id)`
- **THEN** 返回的 span 快照列表 SHALL 包含 `name=dag.execute` 的快照
- **AND** SHALL 包含每个节点的 `name=dag.node` 快照，含 `agenthub.task_id` 和 `agenthub.node_status` 属性
- **AND** eval 规则 SHALL 能通过 `_span_name_matches` 匹配 `dag.node` 并读取 `agenthub.node_status` 判断节点成败

#### Scenario: skipped 节点对 eval 可见

- **WHEN** 一个节点因上游失败被 skip
- **THEN** `run_span_collector` SHALL 记录该节点的 `dag.node` 快照，含 `agenthub.node_status=skipped`
- **AND** eval 规则 SHALL 能检测到该节点被跳过并纳入评分逻辑
