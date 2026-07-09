## MODIFIED Requirements

### Requirement: CustomAgentAdapter SHALL use Chat Completions compatible providers

CustomAgentAdapter SHALL call OpenAI Chat Completions-compatible endpoints for DeepSeek, OpenAI, and Volcano Ark, with provider-specific base URLs and keys. When MCP tools are available, the adapter SHALL merge MCP tool declarations with built-in tool declarations in the `tools` parameter of the Chat Completions request.

#### Scenario: DeepSeek model responds with reasoning
- **WHEN** DeepSeek streams `reasoning_content`
- **THEN** the adapter emits thinking parts
- **AND** includes reasoning content in the assistant message for subsequent turns.

#### Scenario: Custom agent with MCP tools calls the LLM
- **WHEN** `AdapterInput.mcp_tools` contains MCP tool declarations
- **THEN** `call_once()` merges them with built-in `api_tools`
- **AND** passes the combined list as the `tools` parameter to Chat Completions
- **AND** the LLM can choose any tool regardless of source

### Requirement: SDK adapters SHALL expose allowlisted AChat tools through MCP

Claude Code and Codex adapters MUST expose allowlisted AChat tools through adapter-owned MCP bridges rather than consuming per-agent `toolNames`.

#### Scenario: Codex creates and deploys an artifact or workspace build
- **WHEN** Codex calls the AChat MCP `write_artifact`, `deploy_artifact`, or `deploy_workspace` tool
- **THEN** the adapter translates the MCP result into `artifact.create` or `deploy.status`.

#### Scenario: SDK agent asks a structured user question
- **WHEN** Claude Code or Codex calls the AChat MCP `ask_user` tool
- **THEN** AChat routes it through the shared pending question flow.
