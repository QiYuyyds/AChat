# Design: P2 Checkpoint、对抗式验证与智能路由

## Context

P1 完成后，AgentRunner 拥有 ReAct 循环控制权（`_run_react_loop`），每轮的 `messages`、`tool_calls`、`TurnResult` 都在 AgentRunner 内存中。Hooks 系统已建立，`post_turn` 事件每轮触发。这为 P2 的三项优化提供了基础：

1. **Checkpoint**：每轮 `post_turn` 时保存 `messages` 快照，失败后可从断点恢复
2. **对抗式验证**：Orchestrator DAG 完成后、聚合前，对子任务结果做确定性验证
3. **智能路由**：DAG 同波次分派时考虑 agent 负载

当前 Orchestrator 的 retry/replan 机制（`MAX_DISPATCH_ROUNDS=4`）仅依赖子任务的 `report_task_result` 自我报告，没有独立验证。`_execute_dag` 的 `ready.sort()` 是静态权重排序（P0 的 O10），不考虑 agent 当前负载。

约束：新增 DB 表需 migration；CLI adapter 不支持 checkpoint；Verify 不调 LLM。

## Goals / Non-Goals

**Goals:**

- SDK agent run 的 turn 级 checkpoint 保存与恢复
- Orchestrator DAG 完成后新增确定性 Verify 阶段，检测「自称完成但实际有问题」的子任务
- DAG 同波次任务分派考虑 agent 负载，避免单 agent 成为瓶颈

**Non-Goals:**

- 不做 CLI adapter 的 checkpoint（循环在子进程内）
- 不做 workspace 文件系统的 snapshot（checkpoint 只保存 `messages`，不保存文件状态）
- 不做 LLM 驱动的对抗式验证（Verify 是确定性规则检查，不调 LLM）
- 不改 Orchestrator 的 plan/aggregate 阶段
- 不改前端代码

## Decisions

### Decision 1: Checkpoint 只保存 `messages`，不保存 workspace 文件

**选择**: `AgentRunCheckpoint` 表保存 `{run_id, turn_number, messages_json, created_at}`。Resume 时重建 `messages` 列表，不回滚 workspace 文件。

**理由**:
- Workspace 文件回滚需要文件系统 snapshot，成本高且 sandbox 模式下文件可重建
- `messages` 包含完整的对话历史（system + history + user + assistant + tool_results），是 ReAct 循环的全部状态
- 如果 workspace 文件已被修改，resume 后 LLM 会通过 `fs_read` 发现当前文件状态，自然适应
- 最常见的 resume 场景是「LLM 调了几个工具后超时/取消」，此时 workspace 文件状态是正确的

**备选方案**: 保存 workspace 文件 diff → 被否决，增加复杂度且 90% 场景不需要。

### Decision 2: Checkpoint 通过 `post_turn` hook 实现，不侵入 ReAct 循环主逻辑

**选择**: 新增 `hooks/checkpoint.py`，注册 `post_turn` hook。hook 检查 `agent.checkpoint_enabled`，如果启用则调用 `checkpoint_service.save(run_id, turn_number, messages)`。

**理由**:
- 复用 P1 的 Hooks 系统，不侵入 `_run_react_loop` 主逻辑
- 通过 agent 配置启用 = 用户可控
- 如果 hooks 系统不可用，checkpoint 自动跳过（best-effort）

### Decision 3: 每个 run 最多保留 3 个 checkpoint

**选择**: `checkpoint_service.save` 后检查该 run 的 checkpoint 数量，超过 3 个时删除最旧的（保留 LATEST + 2 历史）。

**理由**:
- 3 个 checkpoint 足够覆盖「回退 1-2 轮」的需求
- 无限制保留会导致 DB 膨胀（每个 checkpoint 的 `messages_json` 可能几 KB 到几十 KB）
- 清理在 save 时同步做，不需要后台任务

**备选方案**: 保留所有 checkpoint，定期清理 → 被否决，增加运维负担。

### Decision 4: Verify 阶段是确定性规则检查，不调 LLM

**选择**: `verify_task_result(task, result, evidence)` 函数按 `task_kind` 分发到不同的验证器：

- **code**: 检查 `project` artifact 是否存在且包含 `required_evidence` 中声明的文件路径；检查 `required_commands` 中的命令是否有成功记录
- **document**: 检查 artifact 内容是否覆盖 `expected_outputs` 中声明的所有输出项
- **review**: 检查 review 结论是否引用了 `dependsOn` 中上游任务的 artifact id
- **default**: 不验证（通过）

**理由**:
- LLM 验证会增加延迟和成本，且 LLM 本身可能产生误判
- 确定性验证可重复、可测试、可审计
- 验证失败的判定标准明确（文件不存在、命令未成功、输出项缺失），不会误报

**备选方案**: 引入 Adversarial Agent（LLM 驱动）→ 被否决，成本高且引入非确定性。未来可作为 `on_task_verified` hook 的可选实现。

### Decision 5: Verify 失败触发 replan，复用现有 replan 流程

**选择**: Verify 失败的任务被标记为 `verification_failed`，`should_replan` 检查到有 `verification_failed` 任务时触发 replan。replan context 中包含验证失败原因。

**理由**:
- 复用现有 replan 机制（`MAX_DISPATCH_ROUNDS=4`），不新增流程
- 验证失败原因注入 replan context，让 LLM 知道上一轮哪里出了问题
- 如果 replan 后仍然验证失败，最终在 aggregate 中报告

### Decision 6: AgentLoadTracker 是内存单例，不持久化

**选择**: `AgentLoadTracker` 是进程级单例，维护 `dict[agent_id, LoadInfo]`，`LoadInfo` 包含 `current_tasks: int`、`total_tasks: int`、`avg_duration_ms: float`。

**理由**:
- 负载数据是实时状态，不需要持久化（进程重启后从 0 开始，可接受）
- 内存单例 = 零 DB 开销
- `avg_duration_ms` 从历史 run 记录计算（`AgentRun.started_at` / `finished_at`），初始化时加载

### Decision 7: 智能路由只影响同波次分派，不改 DAG 结构

**选择**: `_execute_dag` 的 `ready` 列表排序后，按 `AgentLoadTracker` 的 `current_tasks` 贪心分配。同一个 agent 在同一波次中最多分配 `MAX_CONCURRENT_TASKS_PER_AGENT`（默认 2）个任务。

**理由**:
- DAG 结构（依赖关系）不变，只改同波次内的分派顺序
- 贪心分配 = 负载最低的 agent 优先拿任务
- 限制单 agent 并发 = 避免一个 agent 被分配 5 个任务而其他 agent 空闲

**备选方案**: 全局调度器（跨波次优化）→ 被否决，DAG 依赖决定了波次顺序，跨波次调度不可行。

## Risks / Trade-offs

- **[Checkpoint 存储成本]** → 每个 checkpoint 几 KB 到几十 KB，3 个/run = 可接受；加自动清理
- **[Resume 后 workspace 不一致]** → LLM 通过 `fs_read` 自然适应当前文件状态；最常见场景（超时/取消）文件状态是正确的
- **[Verify 误报]** → 确定性验证规则保守，只检查明确缺失项；`default` task_kind 不验证
- **[AgentLoadTracker 进程重启丢失]** → 退化为无负载感知（与 P0 的 O10 静态排序相同），可接受
- **[新增 DB 表 migration]** → PostgreSQL `CREATE TABLE` 不锁现有表，migration 风险低

## Migration Plan

1. **Phase 1 — Checkpoint 基础设施**
   - 新增 `AgentRunCheckpoint` 表 + migration
   - 实现 `checkpoint_service.py`（save / load / list / clean）
   - 实现 `hooks/checkpoint.py`（`post_turn` hook）
   - 单元测试

2. **Phase 2 — Resume 功能**
   - AgentRunner `execute_run` 支持 resume 入口
   - 新增 `POST /api/runs/{run_id}/resume` API
   - `_run_react_loop` 接受 `resume_from_checkpoint` 参数
   - 集成测试

3. **Phase 3 — Verify 阶段**
   - 实现 `verify_task_result` 及各 task_kind 验证器
   - Orchestrator `_execute_dag` 后新增 `_verify_stage`
   - 验证失败触发 replan
   - 新增 `on_task_verified` hook 事件
   - 单元测试 + 集成测试

4. **Phase 4 — 智能路由**
   - 实现 `AgentLoadTracker`
   - `_execute_dag` 中加负载感知分派
   - 单元测试

**回退策略**:
- Checkpoint: agent `checkpoint_enabled = False` 时完全跳过
- Verify: `settings.enable_verify_stage = False` 时跳过验证阶段
- 智能路由: `settings.enable_load_aware_routing = False` 时退化为 P0 的静态排序

## Open Questions

1. Checkpoint 的 `messages_json` 是否需要压缩？→ 先不压缩，如果 DB 膨胀明显再加 gzip
2. Verify 阶段是否需要用户可见的事件？→ 是的，新增 `dispatch.verify` 事件（复用 `dispatch.task` 事件格式，status=`verifying` / `verification_passed` / `verification_failed`）
3. `MAX_CONCURRENT_TASKS_PER_AGENT` 是否可配置？→ 是的，通过 `config.py` 配置，默认 2
