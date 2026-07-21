# Adapters

## MODIFIED Requirements

### Requirement: CustomAgentAdapter SHALL use Chat Completions compatible providers

CustomAgentAdapter SHALL call OpenAI Chat Completions-compatible endpoints for DeepSeek, OpenAI, and Volcano Ark, with provider-specific base URLs and keys. For guide agents (`is_guide=true`), AgentRunner SHALL skip baseline tool merging — the guide agent's effective tools are its configured `tool_names` (management tools) plus `ask_user`, without the standard baseline set (`read_attachment` / `fs_*` / `bash`).

#### Scenario: DeepSeek model responds with reasoning
- **WHEN** DeepSeek streams `reasoning_content`
- **THEN** the adapter emits thinking parts
- **AND** includes reasoning content in the assistant message for subsequent turns.

#### Scenario: Guide agent skips baseline tool merging
- **WHEN** AgentRunner assembles the adapter input for a guide agent (`is_guide=true`, `adapter_name='custom'`)
- **THEN** the tool list does NOT include baseline tools (`read_attachment`, `fs_list`, `fs_read`, `fs_write`, `fs_grep`, `fs_glob`, `bash`)
- **AND** the tool list includes only the configured management tools plus `ask_user`.

#### Scenario: Regular custom agent keeps baseline merging
- **WHEN** AgentRunner assembles the adapter input for a non-guide custom agent (`is_guide=false`, `adapter_name='custom'`)
- **THEN** baseline tools ARE merged into the tool list as before
- **AND** the behavior is unchanged from pre-change.
