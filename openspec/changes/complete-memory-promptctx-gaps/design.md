## Context

AgentHub 从 AGI-memory 迁移记忆与 PromptContext 子系统时，Phase 1-2 完成了主干搬运（ShortTerm/LongTerm/Preference/GraphMemory + Splitter/Rewriter/Reranker/HybridStore + 6 个 Source 类 + 4 种 Schema），但 3 个 Source 的 `fetch()` 方法是空壳（直接 `return []`），且部分记忆后处理逻辑（合并落库、LLM 兜底分类）未迁移。

当前现状：
- `PlannerSource.fetch()` 返回空，ReAct 模式下 Agent 无法感知任务进度
- `TaskMemSource.fetch()` 返回空，Agent 无法回顾本任务的历史工具调用
- `ToolStateSource.fetch()` 返回空，Agent 无法看到可用工具列表和近期调用
- `ProfileSource` 只从 Preference 读取，缺少 LTM 中 `category=identity|preference` 的条目
- `_safe_consolidate()` 只改内存，合并结果（删除+更新）不落 PG，重启后丢失
- `classify_memory_content()` 规则不匹配时直接归 "general"，无 LLM 兜底

AGI-memory 原版有完整的 `PlannerProvider`/`TaskMemBuffer`/`ToolStateTracker`/`filter_by_category`/`sync_consolidation_to_db`/`llm_classify_memory` 实现，本变更将这些原版逻辑移植到 AgentHub 的 async 架构中。

## Goals / Non-Goals

**Goals:**
- 让 ReAct/Tool 模式下 prompt 装配的 Planner、TaskMem、ToolState 三个槽位有真实数据
- 让 ProfileSource 同时从 Preference 和 LTM 两个数据源获取身份/偏好信息
- 让记忆合并结果正确同步到 PG，重启后不丢失
- 让规则分类不匹配时通过 LLM 兜底实现 7 类精细分类

**Non-Goals:**
- 不迁移 AGI-memory 的 TaskGraph/GraphRuntime（AgentHub 有自己的 dispatch_plan 架构）
- 不迁移 Sandbox 代码执行沙箱（AgentHub 有 bash 工具 + workspace 隔离）
- 不迁移 AsyncMemoryWriter 后台线程模型（AgentHub 用 asyncio.create_task 替代）
- 不迁移 SubAgents/Planner LLM（AgentHub 有 Orchestrator 架构）
- 不修改 Schema 定义或 SlotKind 类型（Schema 已正确迁移）

## Decisions

### D1: PlannerSource 数据来源 — 从 DispatchPlan 获取状态

**决策**：PlannerSource 通过 `PlannerProvider` 回调从 `DispatchPlanService` 获取当前调度计划状态，而非从 AGI-memory 的 `Planner` LLM 获取。

**理由**：AgentHub 的任务调度架构与 AGI-memory 不同 — AgentHub 用 `DispatchPlanService` + `Orchestrator` 替代了 AGI-memory 的 `Planner` + `GraphRuntime`。PlannerSource 应对接 AgentHub 的调度架构，而非移植 AGI-memory 的 Planner。

**替代方案**：直接移植 AGI-memory 的 `Planner` LLM — 被否决，因为 AgentHub 已有更成熟的 `DispatchPlanService`，重复实现 LLM 规划器会造成架构冲突。

### D2: TaskMemBuffer / ToolStateTracker — async 环形缓冲区

**决策**：将 AGI-memory 的 `threading.RLock` 保护的环形缓冲区改为 `asyncio.Lock` 保护的 async 版本。

**理由**：AgentHub 全栈 async，`threading.RLock` 在 async 上下文中可能导致事件循环阻塞。`asyncio.Lock` 与 AgentHub 的 async 架构一致。

**替代方案**：用 `threading.RLock` 保持与 AGI-memory 一致 — 被否决，因为 AgentHub 是 async 架构，混用锁模型会增加复杂度。

### D3: TaskMemBuffer / ToolStateTracker 注入方式 — 通过 app.state 共享

**决策**：在 `main.py` lifespan 中创建 `TaskMemBuffer` 和 `ToolStateTracker` 实例，挂载到 `app.state`，AgentRunner 通过 `app.state` 引用并 push 数据，PromptAssembler 通过 `app.state` 引用并读取数据。

**理由**：与现有 `prompt_assembler` 和 `memory_service` 的注入方式一致（都通过 `app.state` 共享），无需引入新的依赖注入框架。

**替代方案**：在 AgentRunner 内部创建并传递 — 被否决，因为 PromptAssembler 需要在 AgentRunner 之外的上下文中读取数据（如 Orchestrator 调度时）。

### D4: llm_classify_memory — 独立异步函数，generate_fn 注入

**决策**：将 `llm_classify_memory` 实现为 async 函数，接受 `generate_fn` 参数，在 `extract_memory_from_reply` 中规则分类失败时调用。

**理由**：与现有 `extract_memory_from_reply` 的 async 架构一致，`generate_fn` 已在 `MemoryService` 中注入。

### D5: sync_consolidation_to_db — 在 _safe_consolidate 中直接调用

**决策**：在 `MemoryService._safe_consolidate()` 中，合并完成后直接调用新增的 `_sync_consolidation_to_db(result)` 方法，把 `delete_from_db` 和 `update_in_db` 同步到 PG。

**理由**：合并和落库是同一事务的逻辑延续，不应分离。AGI-memory 原版也是合并后立即落库。

### D6: ProfileSource LTM 数据源 — 通过 filter_by_category 对接

**决策**：在 LongTerm 中新增 `filter_by_category(categories, limit)` async 方法，ProfileSource 通过此方法获取 LTM 中 `category in ["identity", "preference"]` 的条目。

**理由**：对齐 AGI-memory `ProfileSource` 的原版行为（Preference + LTM 双数据源），`filter_by_category` 是 AGI-memory `LongTermCategoryFilter` Protocol 定义的标准接口。

## Risks / Trade-offs

- **[Risk] PlannerProvider 依赖 DispatchPlanService 状态** → DispatchPlanService 的状态可能在非 ReAct 模式下不可用；PlannerSource 在 `get()` 返回 None 时安全降级为空列表，不影响其他模式。

- **[Risk] TaskMemBuffer 内存增长** → 环形缓冲区有 `max_size` 上限（默认 20），超限自动丢弃最早条目，不会无限增长。

- **[Risk] llm_classify_memory 增加额外 LLM 调用** → 仅在规则分类不匹配时触发（约 30-40% 的场景），且 classify prompt 很短（< 200 token），成本可控。

- **[Risk] sync_consolidation_to_db 批量删除可能误删** → `delete_from_db` 中的 ID 来自 consolidate 算法的 dedup/expire 逻辑，已有 cosine 相似度阈值保护；落库时使用参数化 SQL 防注入。

- **[Trade-off] async Lock vs threading.RLock** → async Lock 性能略低于 threading.RLock，但在 async 架构中更安全，不会阻塞事件循环。
