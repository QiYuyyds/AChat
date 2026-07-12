## MODIFIED Requirements

### Requirement: Tool registry SHALL include memory_store
The tool registry SHALL include a `memory_store` tool definition. The tool SHALL be conditionally injected into agent tool sets based on the agent's `memory_enabled` flag.

#### Scenario: memory_store is registered
- **WHEN** the tool registry is initialized
- **THEN** `memory_store` is available as a registered tool

#### Scenario: memory_store is conditionally injected
- **WHEN** `build_adapter_input` constructs the tool list for an agent
- **AND** the agent has `memory_enabled=true` and is a Custom Agent
- **THEN** `memory_store` is included in the tool names passed to the adapter
- **AND** `memory_recall` is also included (if not already present)
