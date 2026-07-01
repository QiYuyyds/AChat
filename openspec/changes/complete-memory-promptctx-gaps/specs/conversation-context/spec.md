## ADDED Requirements

### Requirement: PromptContext Source assembly SHALL fill Planner slot in ReAct mode
In ReAct mode, the ContextAssembler SHALL attempt to fill the Planner slot via PlannerSource. When a PlannerProvider is registered and returns a valid PlannerSnapshot, the Planner slot SHALL contain task status, progress, and next-step ContextItems.

#### Scenario: ReAct mode with active plan
- **WHEN** ContextAssembler.assemble() is called with mode="react" and a PlannerProvider returning a running plan snapshot
- **THEN** the filled RuntimeContext includes non-empty Planner slot items

#### Scenario: ReAct mode without plan
- **WHEN** ContextAssembler.assemble() is called with mode="react" and PlannerProvider returns None
- **THEN** the Planner slot is marked skipped with reason "no active plan"

### Requirement: PromptContext Source assembly SHALL fill TaskMem slot in ReAct mode
In ReAct mode, the ContextAssembler SHALL attempt to fill the TaskMem slot via TaskMemSource. When a TaskMemBuffer has observations, the TaskMem slot SHALL contain step observation ContextItems.

#### Scenario: ReAct mode with task observations
- **WHEN** ContextAssembler.assemble() is called with mode="react" and TaskMemBuffer has 3 observations
- **THEN** the filled RuntimeContext includes up to top_k TaskMem slot items

### Requirement: PromptContext Source assembly SHALL fill ToolState slot in Tool/ReAct mode
In Tool and ReAct modes, the ContextAssembler SHALL attempt to fill the ToolState slot via ToolStateSource. When a ToolStateTracker or ToolRegistryProvider is configured, the ToolState slot SHALL contain available tools and recent call traces.

#### Scenario: Tool mode with registry and tracker
- **WHEN** ContextAssembler.assemble() is called with mode="tool" and both registry and tracker are configured
- **THEN** the filled RuntimeContext includes tool list and recent call trace ContextItems

### Requirement: Memory consolidation results SHALL persist to PostgreSQL
After `LongTerm.consolidate()` or `GraphMemory.graph_aware_consolidate()` completes, the system SHALL sync `ConsolidationResult.delete_from_db` (batch DELETE) and `update_in_db` (per-row UPDATE) to PostgreSQL, so that consolidation effects survive restarts.

#### Scenario: Consolidation deletes duplicate memories from PG
- **WHEN** consolidate() returns ConsolidationResult with delete_from_db=[5, 12, 18]
- **THEN** the corresponding rows in long_term_memory table are deleted from PG

#### Scenario: Consolidation updates merged memories in PG
- **WHEN** consolidate() returns ConsolidationResult with update_in_db containing 2 Items with updated importance and embedding
- **THEN** the corresponding rows in long_term_memory table are updated with new importance and embedding values

#### Scenario: Consolidation with empty result — no DB writes
- **WHEN** consolidate() returns ConsolidationResult with empty delete_from_db and update_in_db
- **THEN** no DELETE or UPDATE statements are executed against PG
