# Tasks: P0 编排优化

## 1. O1 — Tool result 裁剪层

- [x] 1.1 在 `backend/app/services/conversation_context.py` 中新增 `prune_old_tool_results(messages, model, recent_turns=3, prune_threshold=2000)` 函数，遍历消息 parts，将超过阈值的旧 tool_result part 替换为裁剪标记
- [x] 1.2 在 `build_history_for` 的消息序列化流程中，token budget 裁剪前调用 `prune_old_tool_results`
- [x] 1.3 复用 `model_registry.estimate_tokens` 做 token 估算，不引入新依赖
- [x] 1.4 单元测试：旧大 tool_result 被裁剪、旧小 tool_result 保留、最近 3 轮完整保留

## 2. O1 — 旧消息折叠层

- [x] 2.1 在 `conversation_context.py` 中新增 `fold_old_messages(messages, fold_threshold=30, keep_recent=20, pinned_ids=None)` 函数，将最早的消息折叠为一条 system 标记消息
- [x] 2.2 在 `build_history_for` 中，`prune_old_tool_results` 之后调用 `fold_old_messages`
- [x] 2.3 确保 pinned 消息不被折叠（即使超出 keep_recent）
- [x] 2.4 单元测试：超阈值触发折叠、pinned 消息保留、折叠标记包含时间 range

## 3. O1 — LLM 摘要触发优化

- [x] 3.1 在 `context_compaction_service.py` 的 `_maybe_auto_compact_hook` 中，增加 token 估算检查：当估算 token > 87% model_limit 时也触发
- [x] 3.2 从 `model_registry` 获取当前会话 agent 的 model_limit，无 model 信息时回退到纯消息数阈值
- [x] 3.3 单元测试：纯 token 阈值触发、纯消息数触发、两者均不触发不执行

## 4. O4 — Plan 阶段强制 Explore

- [x] 4.1 在 `orchestrator_prompts.py` 的 `ORCHESTRATOR_PLAN_SYSTEM_PROMPT` 中增加探索指令：规划前必须用 `fs_list`/`fs_read` 扫描 workspace
- [x] 4.2 在 `ORCHESTRATOR_PLAN_SYSTEM_PROMPT` 中增加复杂度引导：`simple` 不拆分、`moderate` 适度拆分、`complex` 充分拆分
- [x] 4.3 在 `schemas/dispatch.py` 的 `DispatchPlanItem` 中新增 advisory 字段 `complexity: str | None` 和 `explored: list[str] | None`
- [x] 4.4 `compile_and_validate_dispatch_plan` 不校验这两个字段（advisory），但日志记录
- [x] 4.5 单元测试：simple 复杂度场景不强制拆分、explored 字段可空且不报错

## 5. O10 — DAG 动态优先级

- [x] 5.1 在 `orchestrator.py` 中新增 `_task_priority(task: DispatchPlanItem) -> int` 函数，返回排序权重
- [x] 5.2 在 `_execute_dag` 的 `asyncio.gather` 前，对 `ready` 列表执行 `ready.sort(key=lambda t: -_task_priority(t))`
- [x] 5.3 单元测试：code 任务排在 review 前、有 target_paths 的任务加权、同权重保持稳定排序

## 6. O12 — 聚合深度分析

- [x] 6.1 在 `orchestrator_prompts.py` 的 `build_orchestrator_aggregate_prompt` 中增加一致性检查指令：识别任务间矛盾和冲突
- [x] 6.2 在 `build_aggregate_prompt` 中增加完成度评分指令：fully complete / partially complete / failed + 缺失项说明
- [x] 6.3 在聚合 prompt 中增加下一步推荐指令：对 failed/skipped 任务给出具体修复建议
- [x] 6.4 单元测试：聚合 prompt 包含一致性检查、完成度评分、下一步推荐的关键词

## 7. 集成验证

- [x] 7.1 后端 `ruff check .` 通过
- [x] 7.2 后端 `pytest` 通过（含新增单元测试）
- [x] 7.3 手动验证：长会话（>30 条消息）context 体积明显下降
- [x] 7.4 手动验证：Orchestrator plan 阶段 LLM 先调用 fs_list 再调 plan_tasks
- [x] 7.5 手动验证：群聊 DAG 执行日志中可见任务排序
- [x] 7.6 手动验证：聚合输出包含完成度评分和下一步推荐
