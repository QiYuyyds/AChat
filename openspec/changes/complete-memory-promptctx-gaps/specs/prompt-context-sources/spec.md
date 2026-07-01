## ADDED Requirements

### Requirement: PlannerSource SHALL provide task plan status to prompt assembly
PlannerSource SHALL accept a `PlannerProvider` callback that returns a `PlannerSnapshot` describing the current dispatch plan state (task_id, status, phase, step progress). When the provider returns a valid snapshot, PlannerSource SHALL emit ContextItems containing task status, progress, next step, and interruption recovery hints.

#### Scenario: Active dispatch plan provides status
- **WHEN** PlannerSource.fetch() is called with a PlannerProvider that returns a PlannerSnapshot with status="running", phase="executing", total_steps=5, current_step=2
- **THEN** the returned ContextItems include "任务 {task_id} 状态=running 阶段=executing" and "进度：第 3/5 步"

#### Scenario: No active plan returns empty
- **WHEN** PlannerSource.fetch() is called with a PlannerProvider that returns None
- **THEN** the returned ContextItem list is empty

#### Scenario: Provider not configured returns empty
- **WHEN** PlannerSource.fetch() is called with no PlannerProvider set
- **THEN** the returned ContextItem list is empty

### Requirement: PlannerSnapshot SHALL capture dispatch plan state
`PlannerSnapshot` SHALL be a dataclass with fields: `task_id`, `query`, `status`, `phase`, `total_steps`, `current_step`, `interrupted_at`, `next_step_name`, `next_step_tool`. It SHALL be producible from DispatchPlanService state.

#### Scenario: Snapshot from running dispatch plan
- **WHEN** a dispatch plan is in "executing" phase with 3 of 5 steps completed
- **THEN** PlannerSnapshot.status="running", phase="executing", total_steps=5, current_step=3

### Requirement: TaskMemSource SHALL provide step observations to prompt assembly
TaskMemSource SHALL accept a `TaskMemBuffer` that holds a ring buffer of `StepObservation` entries (step_id, tool_name, result, error, success). When the buffer has entries, TaskMemSource SHALL emit ContextItems summarizing recent tool execution results, respecting the slot's top_k limit.

#### Scenario: Buffer has recent observations
- **WHEN** TaskMemSource.fetch() is called with a buffer containing 3 StepObservation entries (step 1: fs_read success, step 2: fs_write success, step 3: bash failure)
- **THEN** the returned ContextItems include "步骤1 [fs_read]→..." and "步骤2 [fs_write]→..." and "步骤3 [bash] 失败: ..."

#### Scenario: Empty buffer returns empty
- **WHEN** TaskMemSource.fetch() is called with an empty TaskMemBuffer
- **THEN** the returned ContextItem list is empty

#### Scenario: top_k truncation keeps most recent
- **WHEN** TaskMemSource.fetch() is called with a buffer of 10 entries and slot.filter.top_k=5
- **THEN** only the 5 most recent StepObservation entries are included in ContextItems

### Requirement: TaskMemBuffer SHALL be an async ring buffer with max size
`TaskMemBuffer` SHALL provide async `push(StepObservation)`, `reset()`, and `snapshot() -> List[StepObservation]` methods. It SHALL enforce a max_size limit (default 20), discarding the oldest entries when exceeded.

#### Scenario: Push beyond max_size discards oldest
- **WHEN** 25 StepObservation entries are pushed to a TaskMemBuffer with max_size=20
- **THEN** snapshot() returns exactly 20 entries, with the first 5 (oldest) discarded

#### Scenario: Reset clears all entries
- **WHEN** reset() is called on a buffer with 15 entries
- **THEN** snapshot() returns an empty list

### Requirement: ToolStateSource SHALL provide tool registry and call history
ToolStateSource SHALL accept a `ToolRegistryProvider` callback (returns tool name→tool map) and a `ToolStateTracker` (ring buffer of `ToolCallTrace`). It SHALL emit ContextItems listing available tools (name, description, required params) and recent call traces (tool_name, success, summary), respecting top_k.

#### Scenario: Registry provides tool list
- **WHEN** ToolStateSource.fetch() is called with a registry returning {"fs_read": tool1, "bash": tool2}
- **THEN** ContextItems include "fs_read — {description}" and "bash — {description}"

#### Scenario: Tracker provides recent calls
- **WHEN** ToolStateSource.fetch() is called with a tracker containing 2 traces (bash success, fs_read failure)
- **THEN** ContextItems include "近期调用 bash [成功]: ..." and "近期调用 fs_read [失败]: ..."

#### Scenario: Neither registry nor tracker configured
- **WHEN** ToolStateSource.fetch() is called with no registry and no tracker
- **THEN** the returned ContextItem list is empty

### Requirement: ToolStateTracker SHALL be an async ring buffer
`ToolStateTracker` SHALL provide async `record(ToolCallTrace)`, and `snapshot() -> List[ToolCallTrace]` methods. It SHALL enforce a max_size limit (default 10), truncating summaries to 120 characters.

#### Scenario: Record truncates long summaries
- **WHEN** a ToolCallTrace with summary of 200 characters is recorded
- **THEN** snapshot() shows the summary truncated to 120 characters + "…"

### Requirement: ProfileSource SHALL read from both Preference and LTM
ProfileSource SHALL accept both a `PreferenceSnapshotProvider` (for stable user preferences) and a `LongTermCategoryFilter` (for LTM items filtered by category). It SHALL merge results from both sources, with preference items getting score=1.0 and LTM items getting score=importance.

#### Scenario: Both sources provide data
- **WHEN** ProfileSource.fetch() is called with preference={"name": "Alice"} and LTM filter_by_category returns an item with category="identity", content="用户姓名: Alice"
- **THEN** ContextItems include both "name: Alice" (score=1.0, source="profile") and "用户姓名: Alice" (score=importance, source="profile")

#### Scenario: Only preference available
- **WHEN** ProfileSource.fetch() is called with preference data but no LTM provider
- **THEN** ContextItems include only preference key-value pairs

#### Scenario: Only LTM available
- **WHEN** ProfileSource.fetch() is called with LTM data but no preference provider
- **THEN** ContextItems include only LTM items with matching categories

### Requirement: LongTerm SHALL provide filter_by_category method
`LongTerm` SHALL provide an async `filter_by_category(categories: List[str], limit: int) -> List[Item]` method that returns in-memory items whose `category` field matches any of the provided categories, limited to `limit` results, ordered by importance descending.

#### Scenario: Filter by identity and preference
- **WHEN** filter_by_category(["identity", "preference"], limit=10) is called on a LongTerm with 5 identity items and 3 preference items
- **THEN** up to 10 items are returned, ordered by importance descending

#### Scenario: No matching items
- **WHEN** filter_by_category(["nonexistent"], limit=10) is called
- **THEN** an empty list is returned

### Requirement: TaskMemBuffer and ToolStateTracker SHALL be shared via app.state
`TaskMemBuffer` and `ToolStateTracker` instances SHALL be created during application startup and attached to `app.state` for cross-component access. AgentRunner SHALL push observations to these buffers after tool execution.

#### Scenario: AgentRunner pushes tool result to TaskMemBuffer
- **WHEN** AgentRunner completes a tool execution (tool_name="bash", result="success output")
- **THEN** a StepObservation is pushed to app.state.task_mem_buffer with step_id, tool_name, result, success=True

#### Scenario: AgentRunner pushes trace to ToolStateTracker
- **WHEN** AgentRunner completes a tool execution (tool_name="bash", success=True, summary="command output...")
- **THEN** a ToolCallTrace is recorded to app.state.tool_state_tracker
