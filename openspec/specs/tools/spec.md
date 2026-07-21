# Tools

## Purpose

Defines AChat-managed tools, approval boundaries, and adapter-specific tool ownership. Detailed tool specs live in `specs/07-tools.md`.

## Requirements

### Requirement: Tool definitions SHALL be registered centrally

AChat-managed tools MUST be registered through `toolRegistry` with name, description, JSON schema, and handler.

#### Scenario: Custom agent enables a tool
- **WHEN** an agent's `toolNames` includes `fs_read`
- **THEN** CustomAgentAdapter resolves the tool definition from `toolRegistry`.

### Requirement: Attachments SHALL be read through safe tool extraction

`read_attachment` MUST read user-uploaded attachments scoped to the current conversation. Text-like files and PDFs with extractable text SHALL return plain text with bounded length; unsupported binary formats SHALL return metadata instead of raw bytes.

#### Scenario: Agent reads a PDF attachment
- **WHEN** `read_attachment` receives an attachment whose MIME type, filename, or file header identifies it as a PDF
- **THEN** AChat extracts local PDF text before returning the tool result
- **AND** truncates the returned text at the same bounded length used for text files
- **AND** returns a clear note when the PDF has no extractable text and likely needs OCR.

### Requirement: File tools SHALL enforce workspace boundaries

`fs_read`, `fs_write`, and `bash` MUST resolve paths under the conversation effective cwd and reject access outside that tree.

#### Scenario: Agent attempts path traversal
- **WHEN** a tool receives `../../.ssh/id_rsa`
- **THEN** the path check rejects the operation.

### Requirement: Bash SHALL enforce platform blacklist

The bash tool MUST reject commands that match the platform-specific banned pattern list before execution.

#### Scenario: POSIX destructive command is requested
- **WHEN** the command matches `rm -rf /`
- **THEN** the tool refuses to run it.

#### Scenario: POSIX background process inherits stdio
- **WHEN** a bash command starts a background process and the shell script exits
- **THEN** the bash tool MUST NOT wait forever on inherited stdout or stderr
- **AND** it SHOULD clean up the command process group before returning.

### Requirement: Key bash commands SHALL require user approval

AChat MUST require explicit user approval before executing bash commands that are not banned but can materially change dependencies, discard files, or affect host-level runtime state. This approval gate MUST apply to AChat's `bash` tool and SDK command hooks where the adapter exposes a pre-execution permission callback.

#### Scenario: Agent installs dependencies
- **WHEN** an agent requests `pnpm install`
- **THEN** AChat records a pending bash command
- **AND** emits it through the conversation event stream
- **AND** executes the command only after user approval.

### Requirement: Review mode SHALL require write approval

In review mode, file write effects managed by AChat MUST create pending approvals instead of directly mutating workspace files.

#### Scenario: Agent proposes a file write
- **WHEN** `fs_write` is called in review mode
- **THEN** AChat records a pending write
- **AND** waits for explicit user approval.

### Requirement: SDK tool sets SHALL be documented separately

Claude Code and Codex SDK adapters MUST document their built-in tool ownership and any approval bridge limitations instead of pretending those tools are AChat `toolRegistry` tools.

#### Scenario: Codex agent runs in review mode
- **WHEN** a Codex agent is selected
- **THEN** Codex uses read-only sandbox mode
- **AND** AChat exposes only the allowlisted AChat MCP tools to Codex.

### Requirement: AChat SHALL inject tool-call guidance for available tools

AChat MUST append usage guidance and concrete examples for the AChat-managed tools available to the current run. Guidance MUST be scoped to the actual tool set, and MUST call out common argument-shape mistakes for tools whose schemas are often confused.

#### Scenario: Custom agent has file and artifact tools
- **WHEN** a custom agent run is built with `fs_read`, `fs_write`, `read_artifact`, and `write_artifact`
- **THEN** the injected system prompt includes examples for those tools
- **AND** it does not instruct the agent to call unavailable tools such as `plan_tasks`.

#### Scenario: Agent can write artifacts
- **WHEN** a run includes `write_artifact`
- **THEN** the injected guidance warns against empty `write_artifact({})` calls
- **AND** includes a complete document artifact template with `type`, `title`, and `content`.

#### Scenario: SDK adapter receives AChat MCP tools
- **WHEN** a Claude Code or Codex run is built
- **THEN** the injected guidance includes the allowlisted AChat MCP tools exposed by that adapter
- **AND** examples use the exact camelCase argument names accepted by the tool schemas.

#### Scenario: Local workspace code task has file tools
- **WHEN** a run is built for a local workspace
- **AND** the agent has AChat file tools or SDK local file tools
- **THEN** the injected guidance tells the agent to prefer direct workspace file/command tools for project source work
- **AND** tells the agent not to use `write_artifact` for source files that should be written to disk.

### Requirement: Agents SHALL be able to ask structured user questions

AChat MUST provide an `ask_user` tool for finite user choices. The tool SHALL accept 1-4 questions, each with 2-4 options, and SHALL suspend the run until the user answers or the run is aborted.

#### Scenario: Agent needs a blocking finite choice
- **WHEN** an available agent tool call submits `ask_user` with valid questions
- **THEN** AChat records a pending user question
- **AND** emits the pending question through the conversation event stream
- **AND** returns the selected answers to the agent after the user responds.

#### Scenario: Orchestrator plan has a key ambiguity
- **WHEN** the Orchestrator plan stage needs a blocking clarification expressible as 2-4 options
- **THEN** the plan stage may call `ask_user` before `plan_tasks`
- **AND** the aggregate stage does not expose `ask_user`.

### Requirement: Web app artifacts SHALL be deployable to preview URLs

AChat MUST provide a `deploy_artifact` tool that accepts a web app artifact id and returns a deployment status record with a preview path. The tool MUST create a local static deployment and SHOULD additionally publish it to a configured external static directory.

#### Scenario: Agent deploys a web app artifact
- **WHEN** `deploy_artifact` receives a valid `web_app` artifact id
- **THEN** it returns a ready deployment record
- **AND** the record points at the local deployment preview route when no external publish target is configured.

#### Scenario: Agent deploys with external static publishing configured
- **WHEN** `deployment_publish_enabled` is true
- **AND** `deployment_publish_dir` and `deployment_public_base_url` are set
- **THEN** the tool publishes public deployment files to the configured directory
- **AND** returns the public URL as the primary preview path
- **AND** includes a local preview fallback.

#### Scenario: Agent deploys a non-web artifact
- **WHEN** `deploy_artifact` receives a document, image, or missing artifact id
- **THEN** it returns a failed deployment record with a user-visible reason.

### Requirement: Workspace static directories SHALL be deployable to preview URLs

AChat MUST provide a `deploy_workspace` tool that accepts a static output directory inside the current workspace and returns a deployment status record. The tool MUST copy existing static files only; it MUST NOT run build commands. Workspace deployments MUST enforce workspace path isolation, reject missing or non-directory sources, require an HTML entry file, and exclude private or dependency directories such as `.agenthub`, `.git`, and `node_modules`.

#### Scenario: Agent deploys a built local project
- **WHEN** `deploy_workspace` receives `path="dist"` and `dist/index.html` exists inside the conversation workspace
- **THEN** it creates a ready deployment record
- **AND** the record has `sourceType="workspace"` and `workspacePath="dist"`.

#### Scenario: Slash deploy has no artifact candidates
- **WHEN** a user sends `/deploy`
- **AND** the conversation has no `web_app` artifact candidates
- **AND** a common static output directory such as `dist`, `build`, `out`, or `client/dist` exists with `index.html`
- **THEN** AChat deploys that workspace directory and inserts a `deploy_status` message part.

### Requirement: Child tasks SHALL report semantic task outcomes

AChat MUST provide a `report_task_result` tool for Orchestrator-dispatched child runs. The tool SHALL accept `status`, `summary`, optional `acceptanceResults`, and optional `blockers`, and SHALL not create artifacts or mutate workspace files.

#### Scenario: Child reports completion
- **WHEN** a child run calls `report_task_result` with `status="complete"`
- **THEN** AgentRunner can use that structured report as the semantic task outcome.

#### Scenario: Child reports blocked work
- **WHEN** a child run calls `report_task_result` with `status="blocked"`
- **THEN** the dispatch task is treated as not complete
- **AND** blocker details remain available to aggregation.

### Requirement: Management tools SHALL be registered and guide-agent-only

AChat SHALL register 7 management tools in the tool registry: `manage_agents`, `manage_skills`, `manage_mcp`, `manage_documents`, `manage_memory`, `manage_profile`, `manage_conversations`. Each tool SHALL accept an `action` parameter to dispatch to the appropriate sub-operation. Management tools SHALL only be injected into guide agents (`is_guide=true`); AgentRunner SHALL filter them out for non-guide agents even if mistakenly listed in `tool_names`. All management tool handlers SHALL use `ToolContext.user_id` for data isolation.

#### Scenario: manage_agents creates a custom agent
- **WHEN** the guide agent calls `manage_agents(action=create, name="Python 程序员", adapter_name="custom", model_provider="deepseek", model_id="deepseek-v4-flash", tool_names=[...])`
- **THEN** the tool handler creates a new custom agent owned by `ToolContext.user_id`
- **AND** returns the serialized agent row.

#### Scenario: manage_memory lists long-term memories
- **WHEN** the guide agent calls `manage_memory(action=list, memory_type="long_term")`
- **THEN** the tool handler returns all long-term memories for `ToolContext.user_id`
- **AND** excludes other users' memories.

#### Scenario: manage_memory optimizes memories with a user-confirmed plan
- **WHEN** the guide agent calls `manage_memory(action=optimize, plan={delete_ids: [...], merge_groups: [...], update_ids: [...]})`
- **THEN** the tool handler deletes the specified memories, creates merged memories with embeddings, and updates attributes
- **AND** returns a summary of the operations performed.

#### Scenario: manage_conversations searches messages
- **WHEN** the guide agent calls `manage_conversations(action=search, query="worktree")`
- **THEN** the tool handler calls `search_service.search_messages` scoped to `ToolContext.user_id`
- **AND** returns matching messages with conversation title, role, time, and snippet.

#### Scenario: Non-guide agent attempts to use a management tool
- **WHEN** a non-guide agent's `tool_names` includes `manage_agents`
- **THEN** AgentRunner filters `manage_agents` out during tool injection
- **AND** the tool is not available at runtime.

### Requirement: Management tools SHALL enforce confirm parameter for destructive actions

Management tool handlers SHALL require a `confirm=true` parameter for `delete` actions and batch operations. If `confirm` is not `true`, the handler SHALL return an error instructing the LLM to confirm via `ask_user` first. This is a hard fallback to the system prompt's soft requirement.

#### Scenario: Delete without confirm
- **WHEN** the guide agent calls `manage_agents(action=delete, agent_id=X, confirm=false)`
- **THEN** the tool handler returns an error message
- **AND** the deletion does NOT occur.

#### Scenario: Delete with confirm
- **WHEN** the guide agent calls `manage_agents(action=delete, agent_id=X, confirm=true)`
- **AND** agent X is a non-builtin agent owned by the current user
- **THEN** the tool handler deletes agent X
- **AND** returns a success summary.

### Requirement: Management tools SHALL emit guide_side_effect events on success

When a management tool successfully executes a create/update/delete/refresh operation, the tool handler SHALL emit a `guide_side_effect` SSE event with `target` and `action` fields so the frontend can refresh the corresponding panel.

#### Scenario: Agent created successfully
- **WHEN** `manage_agents(action=create)` succeeds
- **THEN** the tool handler emits `guide_side_effect` with `target='agents'`, `action='create'`.

#### Scenario: Document refreshed successfully
- **WHEN** `manage_documents(action=refresh, document_id=X)` succeeds
- **THEN** the tool handler emits `guide_side_effect` with `target='documents'`, `action='refresh'`.
