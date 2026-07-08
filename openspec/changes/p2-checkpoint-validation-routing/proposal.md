# Proposal: P2 Checkpoint、对抗式验证与智能路由

## Why

P1 完成后，AgentRunner 拥有了 ReAct 循环控制权和 Hooks 系统，但仍有三个架构短板：1）长时间 Agent run（如 20 轮工具调用）在中途失败或被取消后无法从断点恢复，用户只能从头重跑；2）Orchestrator 的 retry/replan 仅基于 `report_task_result` 的自我报告，缺少独立验证，子任务「自称完成但实际有 bug」的情况无法被检测；3）任务分派只按 DAG 依赖排序，不考虑 agent 的历史表现和负载，同一个 agent 可能被连续分配多个任务成为瓶颈。

P2 依赖 P1 的 Hooks 系统（checkpoint 通过 `post_turn` hook 保存）和 ReAct 循环上提（turn 级断点需要循环控制权在 AgentRunner）。

## What Changes

### O5: Checkpoint & Resume — 基于 Turn 的断点保存与恢复

- AgentRunner 的 ReAct 循环中，每轮结束后通过 `post_turn` hook 保存 checkpoint：`{run_id, turn_number, messages, tool_calls_history, artifact_ids}`
- Checkpoint 存储在 `agent_run_checkpoints` 表（**新增 DB 表**），字段：`id, run_id, turn_number, messages_json, created_at`
- 新增 API 端点 `POST /api/runs/{run_id}/resume` — 从最新 checkpoint 恢复运行
- Resume 时重建 `messages` 列表，跳过已完成的 turn，从下一轮继续
- CLI adapter 不支持 checkpoint（循环在子进程内，无法 turn 级保存）
- Checkpoint 保留策略：每个 run 最多保留 3 个 checkpoint（LATEST + 2 历史），超限自动清理

### O6: 对抗式验证 — Adversarial Agent 质疑子任务结果

- Orchestrator DAG 执行完成后、聚合阶段前，新增 **Verify 阶段**
- Verify 阶段对 `complete` 状态的子任务结果进行独立验证：
  - Code 任务：检查 `project` artifact 是否包含预期文件、`required_evidence` 中的命令是否真的成功
  - Document 任务：检查 artifact 内容是否覆盖 `expected_outputs` 中声明的所有输出项
  - Review 任务：检查 review 结论是否引用了实际的上游 artifact
- Verify 不调 LLM（确定性验证），通过 `verify_task_result(task, result, evidence)` 函数实现
- 验证失败的任务被标记为 `verification_failed`，触发 replan（复用现有 replan 流程）
- 新增 `on_task_verified` hook 事件，允许自定义验证逻辑

### O7: 智能路由 — 基于 Agent 负载和历史的任务分派优化

- `_execute_dag` 中同一波次的任务分派，从「先排序再 gather」改为「考虑 agent 负载的贪心分配」
- 新增 `AgentLoadTracker`：跟踪每个 agent 的当前并发任务数和历史平均执行时间
- 同一波次中，如果多个任务可以分给不同 agent，优先分给当前负载最低的 agent
- 同一个 agent 在同一波次中最多分配 `MAX_CONCURRENT_TASKS_PER_AGENT`（默认 2）个任务
- 负载数据存在内存（`AgentLoadTracker` 单例），不持久化

## Capabilities

### New Capabilities

- `run-checkpoint`: Agent run 的 turn 级断点保存、恢复与清理协议

### Modified Capabilities

- `orchestrator`: DAG 执行后新增 Verify 阶段；`_execute_dag` 新增 agent 负载感知的任务分派
- `lifecycle-hooks`: 新增 `on_task_verified` hook 事件类型
- `core-domain`: `AgentRun` 新增 `checkpoint_enabled` 属性（控制是否启用 checkpoint）

## Impact

### 代码影响

- `backend/app/db/models.py` — **新增 `AgentRunCheckpoint` 表**
- `backend/app/services/checkpoint_service.py` — **新增文件**，save / load / list / clean checkpoints
- `backend/app/services/hooks/checkpoint.py` — **新增文件**，`post_turn` hook 保存 checkpoint
- `backend/app/services/orchestrator.py` — `_execute_dag` 后新增 `_verify_stage`；`_execute_dag` 中加 `AgentLoadTracker`
- `backend/app/services/agent_load_tracker.py` — **新增文件**，跟踪 agent 并发和历史耗时
- `backend/app/services/agent_runner.py` — `execute_run` 支持 resume 逻辑
- `backend/app/api/runs.py` — 新增 `POST /api/runs/{run_id}/resume` 端点
- `backend/app/services/hook_registry.py` — `HookEvent` 新增 `on_task_verified`

### API 影响

- 新增 `POST /api/runs/{run_id}/resume` — 从 checkpoint 恢复运行
- 新增 `GET /api/runs/{run_id}/checkpoints` — 列出可用 checkpoint

### 依赖影响

- 无新增外部依赖
- **新增 DB 表** `agent_run_checkpoints`（需要 migration）
- 不改 StreamEvent 协议

### 测试影响

- `checkpoint_service` 保存/加载/清理的单元测试
- `_run_react_loop` resume from checkpoint 的集成测试
- `_verify_stage` 对 code/document/review 任务的验证测试
- `AgentLoadTracker` 并发计数和贪心分配测试
- 回归测试：不启用 checkpoint 时行为不变

### 迁移风险

- 新增 DB 表需要 migration，但 PostgreSQL ALTER TABLE 不锁表
- Verify 阶段可能增加总执行时间（确定性验证 <100ms per task），可接受
- `AgentLoadTracker` 是内存状态，进程重启后丢失（可接受，退化为无负载感知）
