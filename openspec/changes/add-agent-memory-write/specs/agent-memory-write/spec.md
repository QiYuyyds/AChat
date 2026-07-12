## ADDED Requirements

### Requirement: Agents with memory_enabled SHALL have a memory_store tool
Custom Agents with `memory_enabled=true` SHALL automatically receive the `memory_store` tool in their tool set. CLI Agents (Claude/Codex) and Mock Agents SHALL NOT receive this tool.

#### Scenario: Custom Agent with memory_enabled
- **WHEN** a Custom Agent has `memory_enabled=true`
- **THEN** `memory_store` is included in its tool set
- **AND** `memory_recall` is also included (if not already)

#### Scenario: Custom Agent without memory_enabled
- **WHEN** a Custom Agent has `memory_enabled=false` (default)
- **THEN** `memory_store` is NOT in its tool set

#### Scenario: CLI Agent
- **WHEN** the agent is a CLI Agent (Claude/Codex)
- **THEN** `memory_store` is NOT injected, regardless of `memory_enabled`

### Requirement: memory_store SHALL enforce category whitelist
The `memory_store` tool SHALL only accept `category` values of `fact`, `policy`, or `tool_failure`. Any other category SHALL be rejected with an error message.

#### Scenario: Valid category
- **WHEN** `memory_store(content="User uses React 19", category="fact", importance=0.7)` is called
- **THEN** the memory is stored in LTM with `scope='agent'`

#### Scenario: Invalid category
- **WHEN** `memory_store(content="...", category="general", importance=0.5)` is called
- **THEN** an error is returned: "category must be one of: fact, policy, tool_failure"

### Requirement: memory_store SHALL enforce importance floor
The `memory_store` tool SHALL reject any write with `importance < 0.3`.

#### Scenario: Importance too low
- **WHEN** `memory_store(content="...", category="fact", importance=0.2)` is called
- **THEN** an error is returned: "importance must be >= 0.3"

### Requirement: memory_store SHALL enforce per-run rate limiting
The `memory_store` tool SHALL limit writes to a maximum of 3 per agent run. Writes exceeding this limit SHALL be rejected with a rate-limit error.

#### Scenario: Within limit
- **WHEN** an agent calls `memory_store` 3 times in one run
- **THEN** all 3 writes succeed

#### Scenario: Exceeds limit
- **WHEN** an agent calls `memory_store` a 4th time in the same run
- **THEN** an error is returned: "memory_store rate limit: max 3 writes per agent run"

### Requirement: memory_store SHALL return agent memory count
The `memory_store` tool response SHALL include the current total number of agent-scoped memories for the calling agent. This provides the agent with awareness of its memory footprint.

#### Scenario: Successful store returns count
- **WHEN** `memory_store` succeeds
- **THEN** the response includes `{"stored": true/false, "agent_memory_count": N}`
- **AND** N is the total count of agent-scoped memories for this agent

### Requirement: memory_store SHALL reuse existing dedup logic
The `memory_store` handler SHALL route through `LongTerm.store_classified()` to reuse the existing cosine dedup (similarity >= 0.95 → update existing item instead of inserting).

#### Scenario: Duplicate memory updates existing
- **WHEN** `memory_store` is called with content very similar to an existing agent memory
- **THEN** the existing memory's importance/tags are updated
- **AND** `stored=false` is returned (indicating dedup hit)
- **AND** no new LTM row is inserted
