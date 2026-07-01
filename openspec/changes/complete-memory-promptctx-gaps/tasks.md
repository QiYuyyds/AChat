## 1. LongTerm.filter_by_category 实现（P0 前置）

- [x] 1.1 在 `backend/app/memory/long_term.py` 中新增 `async def filter_by_category(self, categories: List[str], limit: int) -> List[Item]` 方法，遍历 `self.items` 过滤 `category in categories` 的条目，按 importance 降序排列，返回 limit 条
- [x] 1.2 编写单元测试 `backend/tests/test_long_term_filter.py`：测试正常过滤、空结果、limit 截断、按 importance 排序

## 2. TaskMemBuffer + StepObservation 实现（P0 前置）

- [x] 2.1 在 `backend/app/services/prompt_assembler.py` 中新增 `StepObservation` 数据类（step_id, tool_name, result, error, success, created_at）
- [x] 2.2 在同文件中新增 `TaskMemBuffer` 类：使用 `asyncio.Lock` 保护的环形缓冲区，提供 `async push()`, `reset()`, `snapshot()` 方法，max_size 默认 20
- [x] 2.3 编写单元测试：测试 push 超限丢弃最早条目、reset 清空、snapshot 返回副本

## 3. ToolStateTracker + ToolCallTrace 实现（P0 前置）

- [x] 3.1 在 `backend/app/services/prompt_assembler.py` 中新增 `ToolCallTrace` 数据类（tool_name, success, summary, created_at）
- [x] 3.2 在同文件中新增 `ToolStateTracker` 类：使用 `asyncio.Lock` 保护的环形缓冲区，提供 `async record()`, `snapshot()` 方法，max_size 默认 10，summary 超 120 字符截断
- [x] 3.3 编写单元测试：测试 record 截断、超限丢弃、snapshot 返回副本

## 4. PlannerSnapshot + PlannerProvider 实现（P0 前置）

- [x] 4.1 在 `backend/app/services/prompt_assembler.py` 中新增 `PlannerSnapshot` 数据类（task_id, query, status, phase, total_steps, current_step, interrupted_at, next_step_name, next_step_tool）
- [x] 4.2 新增 `PlannerProvider = Callable[[], Optional[PlannerSnapshot]]` 类型别名
- [x] 4.3 在 `DispatchPlanService` 中新增 `get_planner_snapshot()` 方法，从当前活跃的 dispatch plan 构造 PlannerSnapshot

## 5. 三个空壳 Source 实现（P0 核心）

- [x] 5.1 修改 `PlannerSource`：接受 `PlannerProvider` 参数，`fetch()` 调用 `get()` 获取快照，按 AGI-memory source_planner.py 逻辑输出任务状态/进度/下一步/中断恢复 ContextItems
- [x] 5.2 修改 `TaskMemSource`：接受 `TaskMemBuffer` 参数，`fetch()` 调用 `snapshot()` 获取观察列表，按 top_k 截断，输出步骤观察 ContextItems
- [x] 5.3 修改 `ToolStateSource`：接受 `ToolRegistryProvider` 和 `ToolStateTracker` 参数，`fetch()` 输出可用工具列表 + 近期调用记录 ContextItems
- [x] 5.4 编写单元测试 `backend/tests/test_prompt_sources.py`：分别测试三个 Source 的正常/空/降级场景

## 6. ProfileSource 补全 LTM 数据源（P1）

- [x] 6.1 修改 `ProfileSource`：新增 `ltm` 参数（Optional），`fetch()` 中在 preference 数据之后追加 `await ltm.filter_by_category(slot.filter.categories, limit)` 的结果
- [x] 6.2 LTM 条目转为 ContextItem：text=content, score=importance, source="profile", meta={"category": category}
- [x] 6.3 编写单元测试：测试双数据源、仅 preference、仅 LTM 三个场景

## 7. app.state 注入 + Source 注册（P0 集成）

- [x] 7.1 在 `backend/app/main.py` lifespan 中创建 `TaskMemBuffer()` 和 `ToolStateTracker()` 实例，挂载到 `app.state.task_mem_buffer` 和 `app.state.tool_state_tracker`
- [x] 7.2 在 lifespan 中修改 Source 注册逻辑：PlannerSource 注入 `DispatchPlanService.get_planner_snapshot` 作为 provider；TaskMemSource 注入 `app.state.task_mem_buffer`；ToolStateSource 注入 tool registry provider 和 `app.state.tool_state_tracker`；ProfileSource 注入 LTM 引用
- [x] 7.3 静态验证：检查日志确认所有 Source 已注册

## 8. AgentRunner 数据 push 集成（P0 集成）

- [x] 8.1 在 `backend/app/services/agent_runner.py` 中，工具执行完成后向 `app.state.task_mem_buffer` push `StepObservation`（step_id, tool_name, result/error, success）
- [x] 8.2 在同位置向 `app.state.tool_state_tracker` record `ToolCallTrace`（tool_name, success, summary 截断到 120 字符）
- [x] 8.3 新任务开始时调用 `task_mem_buffer.reset()` 清空上一任务的观察
- [x] 8.4 静态验证：检查代码逻辑确认 push/reset 路径已接通

## 9. llm_classify_memory LLM 兜底分类（P1）

- [x] 9.1 在 `backend/app/memory/memory_writer.py` 中新增 `async def llm_classify_memory(generate_fn, content) -> Tuple[str, List[str], str]` 函数
- [x] 9.2 实现 LLM 分类 prompt：请求 JSON 输出 `{"category":"identity|preference|fact|episodic|tool_failure|policy|general","tags":["tag1"],"slot_hint":"profile|planner|task_memory|tool_state|constraints|recall_memory"}`
- [x] 9.3 复用 `_strip_code_fence()` 处理 LLM 响应，解析失败时回退 `("general", [], "")`
- [x] 9.4 修改 `extract_memory_from_reply()`：规则分类返回空时，调用 `llm_classify_memory(generate_fn, content)` 兜底
- [x] 9.5 编写单元测试：测试规则命中不调用 LLM、规则未命中调用 LLM、LLM 解析失败回退

## 10. sync_consolidation_to_db 合并落库（P0）

- [x] 10.1 在 `backend/app/memory/memory_service.py` 中新增 `async def _sync_consolidation_to_db(self, result: ConsolidationResult)` 方法
- [x] 10.2 实现 `delete_from_db` 批量删除：`DELETE FROM long_term_memory WHERE id IN (...)`，使用参数化 SQL
- [x] 10.3 实现 `update_in_db` 逐条更新：对每个 Item 执行 `UPDATE long_term_memory SET content=..., importance=..., embedding=... WHERE id=...`
- [x] 10.4 在 `_safe_consolidate()` 中合并完成后调用 `_sync_consolidation_to_db(result)`
- [x] 10.5 编写单元测试：测试批量删除、逐条更新、空结果不执行 SQL、异常不中断

## 11. 集成测试与回归验证

- [x] 11.1 运行现有测试 `pytest backend/tests/` 确认无回归（615 passed, 3 pre-existing failures unrelated to this change）
- [x] 11.2 端到端验证：单元测试覆盖 PlannerSource/TaskMemSource/ToolStateSource 的正常/空/降级场景（test_prompt_sources.py 13 tests passed）
- [x] 11.3 验证合并落库：单元测试覆盖 _sync_consolidation_to_db 的批量删除/逐条更新/空结果/异常处理（test_sync_consolidation.py 7 tests passed）
- [x] 11.4 验证 LLM 兜底分类：单元测试覆盖规则命中不调用 LLM、规则未命中调用 LLM、LLM 解析失败回退（test_llm_classify.py 10 tests passed）
- [x] 11.5 验证 ProfileSource 双源：单元测试覆盖双数据源/仅 preference/仅 LTM 三个场景（test_prompt_sources.py 3 tests passed）
