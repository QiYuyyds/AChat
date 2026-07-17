## ADDED Requirements

### Requirement: AChat SHALL conditionally expose code_explore

AChat MUST register `code_explore(query)`. AgentRunner SHALL auto-inject it for Custom runs only when local Workspace source intelligence is enabled and ready. Claude/Codex SHALL access the same ToolDef through existing `achat-tools` MCP Bridge; CodeGraph itself MUST NOT use MCP.

#### Scenario: Ready Custom run
- **WHEN** a Custom run starts in a ready Workspace
- **THEN** `code_explore` is injected without modifying Agent rows or presets.

#### Scenario: Non-ready Custom run
- **WHEN** disabled/building/failed/interrupted
- **THEN** it is not injected and existing tools remain unchanged.

#### Scenario: CLI Agent calls tool
- **WHEN** Claude/Codex calls the prefixed AChat tool
- **THEN** Bridge routes to the common handler and readiness checks.

### Requirement: Guidance SHALL be scoped and degradable

Guidance SHALL recommend `code_explore` for structure, call paths and impact, while permitting file tools for exact lines, unsupported files, low-confidence or failures.
