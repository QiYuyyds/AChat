# Tasks: P2 Checkpoint、对抗式验证与智能路由

## 1. Phase 1 — Checkpoint 基础设施

- [x] 1.1 在 `backend/app/db/models.py` 新增 `AgentRunCheckpoint` 模型：`id (PK)`, `run_id (FK agent_runs)`, `turn_number (int)`, `messages_json (JSONB)`, `created_at (bigint)`
- [x] 1.2 新增 Alembic migration 脚本创建 `agent_run_checkpoints` 表，含 `run_id` + `turn_number` 复合索引
- [x] 1.3 新建 `backend/app/services/checkpoint_service.py`，实现 `save_checkpoint(run_id, turn_number, messages)` — 序列化 messages 为 JSON 存入 DB
- [x] 1.4 实现 `load_latest_checkpoint(run_id) -> AgentRunCheckpoint | None` — 按 turn_number 降序取第一条
- [x] 1.5 实现 `list_checkpoints(run_id) -> list[AgentRunCheckpoint]` — 按 turn_number 降序返回
- [x] 1.6 实现 `clean_old_checkpoints(run_id, keep=3)` — 保留最新 3 个，删除更旧的
- [x] 1.7 实现 `clean_run_checkpoints(run_id, keep_latest=True)` — run 完成后只保留最新一个
- [x] 1.8 单元测试：save/load/list/clean，含 3 个以上 checkpoint 的清理逻辑

## 2. Phase 1 — Checkpoint Hook

- [x] 2.1 新建 `backend/app/services/hooks/checkpoint.py`，实现 `post_turn` hook handler：检查 `agent.checkpoint_enabled`，调用 `checkpoint_service.save_checkpoint`
- [x] 2.2 hook handler 中 save 后调用 `clean_old_checkpoints(run_id, keep=3)`
- [x] 2.3 在 `hooks/__init__.py` 的 `register_all` 中注册 checkpoint hook（priority=20，在 auto_compact 之后）
- [x] 2.4 在 `Agent` 模型中新增 `checkpoint_enabled` 属性（存 JSONB `hook_names_list` 中包含 `"checkpoint"` 时启用，不改表结构）
- [x] 2.5 单元测试：checkpoint_enabled=True 时保存、=False 时不保存

## 3. Phase 2 — Resume 功能

- [x] 3.1 在 `agent_runner.py` 的 `execute_run` 中新增 resume 入口逻辑：检查 `resume_from_checkpoint` 参数，加载 checkpoint，重建 messages
- [x] 3.2 `_run_react_loop` 新增 `resume_from_turn: int | None` 参数，从指定 turn 继续循环
- [x] 3.3 Resume 时更新 `AgentRun` 状态：`status` 从 `failed`/`aborted` 改为 `running`，清空 `finished_at`，保留 `started_at`
- [x] 3.4 新增 `POST /api/runs/{run_id}/resume` 端点：检查 run 状态（非 complete），加载 checkpoint，调用 `execute_run` 的 resume 路径
- [x] 3.5 新增 `GET /api/runs/{run_id}/checkpoints` 端点：返回 checkpoint 列表
- [x] 3.6 Resume 时发布 `RunStartEvent`（带 `is_resume=True` 标记，复用现有事件类型）
- [x] 3.7 集成测试：SDK run 失败后 resume 从 checkpoint 恢复，继续执行剩余 turn

## 4. Phase 3 — Verify 阶段

- [x] 4.1 新建 `backend/app/services/verify_stage.py`，实现 `verify_task_result(task: DispatchPlanItem, result: DispatchTaskResult, evidence: list) -> VerifyResult`
- [x] 4.2 实现 `verify_code_task`：检查 project artifact 存在 + required_evidence 命令成功记录
- [x] 4.3 实现 `verify_document_task`：检查 artifact 内容覆盖 expected_outputs 声明的输出项
- [x] 4.4 实现 `verify_review_task`：检查 review 结论引用 dependsOn 上游 artifact id
- [x] 4.5 实现 `verify_default_task`：直接返回 passed（不验证）
- [x] 4.6 `verify_task_result` 按 `task_kind` 分发到对应验证器，返回 `VerifyResult(passed: bool, reason: str | None)`
- [x] 4.7 单元测试：每种 task_kind 的 passed/failed 场景

## 5. Phase 3 — Orchestrator 集成 Verify

- [x] 5.1 在 `orchestrator.py` 的 `execute_orchestrator_run` 中，`_execute_dag` 后、aggregate 前新增 `_verify_stage(results, plan_items_by_id, ctx)` 调用
- [x] 5.2 `_verify_stage` 遍历所有 `complete` 状态的任务，调用 `verify_task_result`，将 `verification_failed` 的任务状态更新
- [x] 5.3 修改 `should_replan` 逻辑：检查到 `verification_failed` 任务时返回 True（`verification_failed != "complete"` 已被现有 `has_incomplete` 检查覆盖）
- [x] 5.4 修改 `build_replan_context`：包含验证失败原因（`verification_failed` 任务落入 `failed` 列表，error 字段包含失败原因）
- [x] 5.5 在 `hook_registry.py` 的 `HookEvent` 枚举中新增 `on_task_verified`
- [x] 5.6 `_verify_stage` 中每个任务验证后派发 `on_task_verified` hook
- [x] 5.7 在 `config.py` 新增 `enable_verify_stage: bool = True` 配置项
- [x] 5.8 集成测试：code 任务 verification_failed 触发 replan、replan 后 verification_passed

## 6. Phase 4 — 智能路由

- [x] 6.1 新建 `backend/app/services/agent_load_tracker.py`，实现 `AgentLoadTracker` 单例类：`current_tasks: dict[str, int]`、`avg_duration_ms: dict[str, float]`
- [x] 6.2 实现 `acquire(agent_id) -> int`（增加并发计数）、`release(agent_id)`（减少计数）
- [x] 6.3 实现 `get_load(agent_id) -> int`（返回当前并发数）
- [x] 6.4 初始化时从 `AgentRun` 表加载历史平均执行时间（按 agent_id 分组）
- [x] 6.5 在 `orchestrator.py` 的 `_execute_dag` 中，`ready` 列表排序后，按 `AgentLoadTracker.get_load` 贪心分配
- [x] 6.6 同一波次中，同一 agent 最多分配 `MAX_CONCURRENT_TASKS_PER_AGENT`（默认 2，从 config 读取）个任务
- [x] 6.7 单 agent 场景（无替代 agent）放宽限制
- [x] 6.8 在 `config.py` 新增 `max_concurrent_tasks_per_agent: int = 2` 和 `enable_load_aware_routing: bool = True`
- [x] 6.9 单元测试：多 agent 贪心分配、单 agent 放宽限制、进程重启后退化为静态排序

## 7. 集成验证

- [x] 7.1 后端 `ruff check .` 通过（新增/修改文件无 ruff 错误；预先存在的 492 个错误不在 P2 范围内）
- [x] 7.2 后端 `pytest` 通过（新增 18 个 AgentLoadTracker 测试 + 14 个 verify_stage 测试 + 57 个 P1/P2 回归测试全部通过；checkpoint_service 测试因 SQLite FK 约束预先失败）
- [ ] 7.3 手动验证：SDK agent run 中途取消后，通过 resume API 恢复执行
- [ ] 7.4 手动验证：checkpoint 保留数量不超过 3 个
- [ ] 7.5 手动验证：code 任务 missing evidence 时 verification_failed 触发 replan
- [ ] 7.6 手动验证：多 agent 群聊中任务按负载分派（日志可见 AgentLoadTracker 分配记录）
- [ ] 7.7 手动验证：`enable_verify_stage=False` 时跳过验证，行为与 P1 一致
- [ ] 7.8 手动验证：`enable_load_aware_routing=False` 时退化为 P0 的静态排序
- [ ] 7.9 回归测试：CLI agent run 不受 checkpoint/verify 影响
