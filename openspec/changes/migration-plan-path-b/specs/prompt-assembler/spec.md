## ADDED Requirements

### Requirement: PromptAssembler SHALL define 6 SlotKinds
The assembler MUST support 6 slot kinds: `profile`, `planner`, `task_memory`, `tool_state`, `constraints`, `recall_memory`. Each slot SHALL have a `SlotFilter` with configurable `token_budget`.

#### Scenario: Slot kinds are registered
- **WHEN** the assembler is initialized
- **THEN** all 6 SlotKind values are available for schema definitions

### Requirement: PromptAssembler SHALL define 4 Schema types
The assembler MUST define 4 schemas selecting different slot compositions: `CHAT_SCHEMA` (Constraints + Profile + Recall), `TOOL_SCHEMA` (Constraints + Profile + ToolState + Recall), `REACT_SCHEMA` (Constraints + Planner + TaskMem + ToolState + Profile + Recall), `RAG_SCHEMA` (Constraints + Profile + Recall).

#### Scenario: REACT schema is selected for tool-use conversations
- **WHEN** the query mode is "react"
- **THEN** the assembler uses REACT_SCHEMA with 6 slots including Planner and TaskMem

#### Scenario: CHAT schema is selected for simple conversations
- **WHEN** the query mode is "chat"
- **THEN** the assembler uses CHAT_SCHEMA with 3 slots

### Requirement: PromptAssembler SHALL support 6 Context Sources
The assembler MUST implement 6 source types: ProfileSource (agents.system_prompt + Preference), PlannerSource (orchestrator dispatch_plan), TaskMemSource (agent_runner run context), ToolStateSource (agent_runs + tool_registry), ConstraintsSource (workspace policy), RecallSource (memory_service.ltm.recall_by_filter).

#### Scenario: ProfileSource fetches agent profile
- **WHEN** ProfileSource is called for a slot
- **THEN** it returns the agent's system_prompt combined with stored preferences

#### Scenario: RecallSource fetches relevant memories
- **WHEN** RecallSource is called with a query embedding
- **THEN** it calls `memory_service.ltm.recall_by_filter` and returns matching memories as ContextItems

### Requirement: PromptAssembler SHALL fill slots concurrently
`assemble()` MUST fill all slots in a schema concurrently using `asyncio.gather`. Individual slot failures SHALL be caught and marked as `skipped` with a reason, without blocking other slots.

#### Scenario: All slots fill successfully
- **WHEN** all sources return data
- **THEN** each FilledSlot contains items from its sources

#### Scenario: One source fails
- **WHEN** PlannerSource raises an exception
- **THEN** the planner slot is marked `skipped=true` with reason
- **AND** other slots are filled normally

### Requirement: PromptAssembler SHALL enforce token budgets
The assembler MUST apply a global token budget (default 2400 chars) and per-slot budgets. Items exceeding the budget SHALL be trimmed by `_trim_by_budget()` in priority order.

#### Scenario: Context exceeds global budget
- **WHEN** the total assembled context exceeds 2400 characters
- **THEN** lower-priority slot items are trimmed first until the budget is met

#### Scenario: Single slot exceeds its budget
- **WHEN** recall_memory slot items exceed `slot_budget_recall` (400 chars)
- **THEN** only the top-priority items within the budget are kept

### Requirement: PromptAssembler SHALL render to OpenAI chat format
The assembled `RuntimeContext` MUST render to system prompt and history messages compatible with OpenAI chat format.

#### Scenario: Assembled context renders correctly
- **WHEN** `ctx.render_system_prompt()` is called
- **THEN** all filled slot items are serialized into a structured system prompt string
- **AND** `ctx.render_history()` returns a message list compatible with OpenAI chat API
