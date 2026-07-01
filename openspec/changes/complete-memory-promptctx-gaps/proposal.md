## Why

AGI-memory 迁移到 AgentHub 后，PromptContext 的 6 个 Source 中有 3 个（PlannerSource、TaskMemSource、ToolStateSource）的 `fetch()` 方法直接返回空列表，导致 ReAct 模式下 prompt 装配的关键上下文（任务进度、步骤观察、工具调用历史）完全缺失。同时，记忆合并结果不落库（重启后丢失）、LLM 兜底分类缺失（分类精度低）、ProfileSource 缺少 LTM 数据源（身份/偏好记忆无法注入 prompt）。这些缺口使记忆系统和 PromptContext 的核心能力无法正常发挥。

## What Changes

- **PlannerSource 实现**：新增 `PlannerProvider` 回调接口和 `PlannerSnapshot` 数据类，让 PlannerSource 能从 Orchestrator/DispatchPlan 获取当前任务状态并装填到 prompt
- **TaskMemSource 实现**：新增 `TaskMemBuffer` 环形缓冲区和 `StepObservation` 数据类，让 TaskMemSource 能从 AgentRunner 工具执行结果中装填步骤观察
- **ToolStateSource 实现**：新增 `ToolStateTracker` 环形缓冲区和 `ToolCallTrace` 数据类，让 ToolStateSource 能装填可用工具列表和近期调用记录
- **LongTerm.filter_by_category 实现**：新增按 category 列表过滤 LTM 条目的方法，供 ProfileSource 获取 identity/preference 类别的记忆
- **ProfileSource 补全**：修改 ProfileSource 使其同时从 Preference 和 LTM 两个数据源获取，对齐 AGI-memory 原版行为
- **sync_consolidation_to_db 实现**：在 MemoryService 的合并流程中增加 PG 落库逻辑，把 `ConsolidationResult.delete_from_db` 和 `update_in_db` 同步到 PostgreSQL
- **llm_classify_memory 实现**：在 memory_writer.py 中增加 LLM 兜底分类（7 类 6 槽），规则分类不匹配时调用 LLM 进行精细分类

## Capabilities

### New Capabilities
- `prompt-context-sources`: PromptContext 六个 Source 的完整数据装填能力，包括 PlannerSource（任务状态）、TaskMemSource（步骤观察）、ToolStateSource（工具调用追踪）三个空壳 Source 的实现，以及 ProfileSource 的 LTM 数据源补全

### Modified Capabilities
- `memory-extraction`: 增加规则分类失败后的 LLM 兜底分类能力（7 类 6 槽），提升记忆分类精度
- `conversation-context`: PromptContext Source 装填行为变更，ReAct/Tool 模式下 Planner、TaskMem、ToolState 槽位从空变为有内容

## Impact

- **后端代码**：
  - `backend/app/services/prompt_assembler.py` — 实现 3 个空壳 Source + 补全 ProfileSource
  - `backend/app/memory/long_term.py` — 新增 `filter_by_category()` 方法
  - `backend/app/memory/memory_service.py` — 增加 `sync_consolidation_to_db` 落库逻辑
  - `backend/app/memory/memory_writer.py` — 新增 `llm_classify_memory()` LLM 兜底分类
  - `backend/app/services/agent_runner.py` — 在工具执行后向 TaskMemBuffer/ToolStateTracker push 数据
- **数据库**：`long_term_memory` 表的合并结果（删除+更新）将正确同步到 PG
- **依赖**：LLM 兜底分类需要 `generate_fn` 可用（已有 DashScope/OpenAI 配置）
- **API**：无接口变更，纯后端能力补全
