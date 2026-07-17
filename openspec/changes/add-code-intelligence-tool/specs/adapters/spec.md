## MODIFIED Requirements

### Requirement: CLI adapters SHALL expose allowlisted AChat tools through MCP

Claude Code and Codex MUST expose `code_explore` through the existing `achat-tools` Bridge. The Bridge dispatches to ToolRegistry with conversation/Workspace context and MUST NOT start CodeGraph MCP. Custom Agent calls the same ToolDef directly without MCP.

#### Scenario: CLI explores code
- **WHEN** CLI Agent calls `mcp__achat-tools__code_explore`
- **THEN** common handler validates Workspace state and invokes managed CodeGraph CLI.
