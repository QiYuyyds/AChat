## MODIFIED Requirements

### Requirement: Long-term memory SHALL support scope-based isolation
LongTermMemory SHALL have a `scope` column (`global` or `agent`) and an `agent_id` column (nullable, required when scope='agent'). Memories with scope='agent' are isolated per-agent; memories with scope='global' are shared across all agents.

#### Scenario: Agent-scoped memory is written
- **WHEN** `extract_memory_from_reply` is called with `agent_id="agt_123"`
- **THEN** the resulting LTM row has `scope='agent'` and `agent_id='agt_123'`
- **AND** the memory is only recalled when agent_id matches

#### Scenario: Global memory is shared
- **WHEN** a memory has `scope='global'` and `agent_id=NULL`
- **THEN** it is recalled regardless of which agent is querying

#### Scenario: Existing data is migrated
- **WHEN** the migration runs on existing `long_term_memory` rows
- **THEN** all rows get `scope='global'` and `agent_id=NULL`
- **AND** no data is lost or moved

### Requirement: Recall SHALL prioritize agent-scoped memories
`LongTerm.recall()` and `recall_by_filter()` SHALL accept an `agent_id` parameter. When provided, recall SHALL first search agent-scoped memories, then fill remaining slots from global memories.

#### Scenario: Agent has enough scoped memories
- **WHEN** `recall(query, top_k=3, agent_id="agt_123")` is called
- **AND** agent "agt_123" has 5 scoped memories matching the query
- **THEN** the top 3 agent-scoped memories are returned
- **AND** global memories are not searched

#### Scenario: Agent needs global fill
- **WHEN** `recall(query, top_k=3, agent_id="agt_123")` is called
- **AND** agent "agt_123" has only 1 scoped memory matching
- **THEN** 1 agent-scoped + 2 global memories are returned

### Requirement: Consolidation SHALL be scoped
`consolidate()` SHALL group items by `(scope, agent_id)` and perform dedup/merge/expire within each group. Cross-scope or cross-agent dedup is not performed.

#### Scenario: Two agents have similar memories
- **WHEN** Agent A has "user likes TypeScript" and Agent B has "user likes TypeScript"
- **THEN** consolidation does NOT merge them (different agent_id)
- **AND** both memories persist independently

### Requirement: Graph memory SHALL respect scope boundaries
`GraphMemory.find_related()` SHALL only expand to nodes with the same `(scope, agent_id)` as the seed. Cross-agent graph expansion is not performed.

#### Scenario: Graph expansion stays within agent scope
- **WHEN** `find_related(mem_id=42)` is called for an agent-scoped memory
- **THEN** only related memories with the same `agent_id` are returned
