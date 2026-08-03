# Adapters

## Purpose

Defines the AgentPlatformAdapter contract and provider-specific boundaries. Detailed adapter notes live in `specs/05-adapter-interface.md`.

## Requirements

### Requirement: Adapters SHALL translate provider output to StreamEvent

Each adapter MUST expose `stream(input, signal)` and yield only AChat `StreamEvent` objects to the application layer.

#### Scenario: Custom model emits tool calls
- **WHEN** Chat Completions streaming returns function tool call deltas
- **THEN** CustomAgentAdapter accumulates arguments
- **AND** emits AChat `tool.call` and `tool.result` events.

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

### Requirement: ClaudeCodeAdapter SHALL spawn Claude Code CLI as subprocess

ClaudeCodeAdapter MUST spawn the `claude` CLI binary as a subprocess via `asyncio.create_subprocess_exec`, communicate via stream-json protocol over stdin/stdout, and translate CLI output events into `StreamEvent`. The adapter SHALL NOT use the Anthropic SDK or implement its own tool loop.

The adapter SHALL use `--permission-mode acceptEdits` (not `bypassPermissions`) so that write-class tool calls emit `control_request` events that the adapter can route through AChat's security infrastructure.

#### Scenario: Claude Code CLI handles read-only tools autonomously

- **WHEN** a Claude Code agent performs a read-only operation (Read, Glob, Grep)
- **THEN** the CLI auto-approves under `acceptEdits` mode without sending a `control_request`
- **AND** the adapter translates the resulting events into `StreamEvent` without interception

#### Scenario: Claude Code CLI requests permission for a bash command

- **WHEN** the CLI emits a `control_request` for a Bash tool call
- **THEN** the adapter parses the request to extract the command string
- **AND** checks the command against `find_banned_pattern()` — denies if blacklisted
- **AND** checks the command against `classify_bash_approval()` — routes to `wait_for_bash_approval()` if approval is required
- **AND** responds with `control_response` `{behavior: "allow"}` or `{behavior: "deny"}` accordingly

#### Scenario: Claude Code CLI requests permission for a file write

- **WHEN** the CLI emits a `control_request` for a Write or Edit tool call
- **THEN** the adapter resolves the target path against the workspace sandbox via `resolve_safe_path()`
- **AND** denies the request if the path escapes the workspace
- **AND** when the conversation is in `review` mode, registers a pending write via `pending_writes.register()` and waits for user decision via `await_pending_decision()`
- **AND** responds with `control_response` `{behavior: "allow"}` or `{behavior: "deny"}` based on the outcome

#### Scenario: Claude Code CLI resumes a prior session

- **WHEN** the adapter receives `AdapterInput.resume_session_id` set
- **THEN** it passes `--resume <session_id>` to the CLI
- **AND** if the resume yields a fresh (different) session id or the run fails, the adapter reports `session_id=""` so the retry-with-fresh-session logic can trigger

#### Scenario: Claude Code CLI is not installed

- **WHEN** `shutil.which("claude")` returns nothing and no `executable_path` is configured
- **THEN** the adapter raises a clear error: "Claude Code CLI not found. Install it with `npm install -g @anthropic-ai/claude-code` or configure executable_path."

#### Scenario: User cancels a running Claude Code agent

- **WHEN** `cancel_event` is set
- **THEN** the adapter closes stdin, waits up to 10s for graceful exit, then terminates and kills the process

#### Scenario: Custom args override

- **WHEN** `adapter_input.custom_args` is provided
- **THEN** the adapter SHALL merge them after built-in args
- **AND** SHALL reject any args in `_claude_blocked_args` (including `--disallowedTools`, `--model`, `--permission-mode`, `--allowedTools`, `--resume`, `--session-id`, `--debug`)
- **AND** SHALL emit a `RunErrorEvent` with a clear message if a blocked arg is detected

#### Scenario: Malformed control_request

- **WHEN** a `control_request` event is received with no recognisable tool name
- **THEN** the adapter SHALL respond with `behavior=deny` and a "Malformed control_request" message
- **AND** SHALL log a warning for diagnostics

### Requirement: ClaudeCodeAdapter SHALL capture and persist CLI session IDs

The adapter MUST capture the `session_id` from the `result` event of each run. AgentRunner MUST persist this `session_id` into the `AgentRun.cli_session_id` DB column (new nullable column). On subsequent runs, `build_adapter_input` MUST query the latest `AgentRun` for the same `conversation_id` + `agent_id` where `cli_session_id IS NOT NULL` and pass it as `resume_session_id`. An in-memory `claude_code_sessions` dict (keyed by `conversation_id:agent_id`) serves as a hot-path cache layer on top of the DB query.

#### Scenario: First run stores session ID in DB

- **WHEN** a Claude Code agent completes its first run in a conversation
- **THEN** the adapter extracts `session_id` from the `result` event
- **AND** the `RunUsageEvent` includes the `session_id` field
- **AND** AgentRunner persists the `session_id` into `AgentRun.cli_session_id` for that run
- **AND** the in-memory `claude_code_sessions` cache is populated with `f"{conversation_id}:{agent_id}"` as key

#### Scenario: Subsequent run resumes session (cache hit)

- **WHEN** the same Claude Code agent starts a second run in the same conversation and the backend has not restarted
- **THEN** `build_adapter_input` reads the session_id from the `claude_code_sessions` in-memory cache (no DB query)
- **AND** passes it as `resume_session_id` to the adapter
- **AND** the adapter passes `--resume <session_id>` to the CLI

#### Scenario: Subsequent run resumes session (cache miss after restart)

- **WHEN** the backend has restarted (in-memory cache is empty) and the same Claude Code agent starts a new run
- **THEN** `build_adapter_input` queries `AgentRun` for the latest run with `conversation_id` + `agent_id` where `cli_session_id IS NOT NULL`, ordered by `started_at DESC`
- **AND** passes the retrieved `cli_session_id` as `resume_session_id`
- **AND** populates the in-memory cache for subsequent runs

#### Scenario: Session resume fails

- **WHEN** `--resume` fails (session expired or not found)
- **THEN** the adapter detects the failure (different session_id returned or run error)
- **AND** clears the in-memory cache entry for this `conversation_id:agent_id`
- **AND** does NOT persist a `cli_session_id` for the failed run (leaves it NULL)
- **AND** retries with a fresh session (no `--resume`)

#### Scenario: Conversation history diverges

- **WHEN** the user deletes, clears, withdraws, or regenerates a message
- **THEN** `conversation_service` calls `clear_claude_code_session(conversation_id)` to drop the in-memory cache
- **AND** the DB query on the next run picks the latest surviving run's `cli_session_id` (if any)

### Requirement: ClaudeCodeAdapter SHALL support image attachments

The adapter MUST include image attachments as `image` content blocks in the stream-json user message, using base64-encoded source data. Non-image file attachments MUST be referenced as a text note with the file path so Claude Code can use its built-in Read tool.

#### Scenario: User uploads an image to a Claude Code agent

- **WHEN** `AdapterInput.attachments` contains an entry with `kind="image"`
- **THEN** the adapter reads the file from `abs_path`
- **AND** base64-encodes the content
- **AND** includes `{"type": "image", "source": {"type": "base64", "media_type": "<mime>", "data": "<base64>"}}` in the user message content array alongside the text block

#### Scenario: User uploads a non-image file to a Claude Code agent

- **WHEN** `AdapterInput.attachments` contains an entry with `kind="file"`
- **THEN** the adapter appends a text note to the prompt: "Attached file: <fileName> (<mimeType>) at <absPath>"
- **AND** Claude Code can use its built-in Read tool to access the file

#### Scenario: No attachments

- **WHEN** `AdapterInput.attachments` is None or empty
- **THEN** the adapter sends a plain text content block (unchanged from current behavior)

### Requirement: ClaudeCodeAdapter SHALL expose dynamic MCP tools

The adapter MUST pass the agent's configured `tool_names` to the MCP Bridge process via a `--tool-names` argument. The MCP Bridge MUST filter its exposed tools to match. The `ACHAT_MCP_TOOL_HINT` system prompt MUST be generated dynamically from the actual tool list rather than hardcoded.

#### Scenario: Agent with custom tool configuration

- **WHEN** a Claude Code agent has `tool_names=["write_artifact", "web_search"]`
- **THEN** the MCP Bridge receives `--tool-names write_artifact,web_search`
- **AND** `tools/list` returns only those tools that are registered in `tool_registry`
- **AND** the system prompt hint lists `mcp__achat-tools__write_artifact` and `mcp__achat-tools__web_search`

#### Scenario: Agent with default tool configuration

- **WHEN** a Claude Code agent has no `tool_names` configured (empty list)
- **THEN** the MCP Bridge falls back to the default `CLI_MCP_TOOL_NAMES` set
- **AND** the system prompt hint lists all default tools

### Requirement: ClaudeCodeAdapter SHALL enforce timeout watchdog

The adapter MUST monitor for semantic inactivity during `_read_events`. If no meaningful output is received within `first_turn_no_progress_timeout` (default 30s) on the first turn, or within `semantic_inactivity_timeout` (default 10 min) at any point, the adapter MUST terminate the run with a timeout status.

#### Scenario: First turn no progress

- **WHEN** the CLI process is alive but emits no semantic progress within 30 seconds of the first turn starting
- **THEN** the adapter marks the run as `timeout`
- **AND** gracefully shuts down the CLI process

#### Scenario: Semantic inactivity mid-run

- **WHEN** the CLI process is alive but emits no semantic progress for 10 minutes at any point during the run
- **THEN** the adapter marks the run as `timeout`
- **AND** gracefully shuts down the CLI process

### Requirement: MCP Bridge SHALL reuse a single event loop

The MCP Bridge SHALL use a single module-level asyncio event loop for all tool executions instead of creating a new loop per call.

#### Scenario: First tool call

- **WHEN** `_execute_tool` is called for the first time
- **THEN** the MCP Bridge SHALL create a module-level event loop and store it
- **AND** SHALL run the async tool handler on that loop

#### Scenario: Subsequent tool calls

- **WHEN** `_execute_tool` is called again
- **THEN** the MCP Bridge SHALL reuse the existing module-level event loop
- **AND** SHALL NOT create a new `asyncio.new_event_loop()`

### Requirement: ClaudeCodeAdapter SHALL block security-critical CLI flags in custom args

The adapter SHALL block `--disallowedTools` and `--model` in custom args to prevent security-critical overrides.

#### Scenario: User passes --disallowedTools via custom_args

- **WHEN** `custom_args` contains `--disallowedTools`
- **THEN** the adapter SHALL reject it and emit a `RunErrorEvent` explaining that `--disallowedTools` is managed by the system

#### Scenario: User passes --model via custom_args

- **WHEN** `custom_args` contains `--model`
- **THEN** the adapter SHALL reject it and emit a `RunErrorEvent` explaining that `--model` is managed by the system

### Requirement: ClaudeCodeAdapter SHALL not accumulate dead code

The adapter SHALL remove the unused `output_parts` accumulator and `DEFAULT_CLAUDE_MODEL` SHALL reference a valid model identifier or be `None`.

#### Scenario: No output_parts accumulation

- **WHEN** `run()` processes events
- **THEN** the adapter SHALL NOT accumulate parts in an `output_parts` list
- **AND** SHALL yield events directly (the unused accumulator is removed)

#### Scenario: Valid DEFAULT_CLAUDE_MODEL

- **WHEN** the adapter needs a fallback model identifier
- **THEN** `DEFAULT_CLAUDE_MODEL` SHALL reference a model that exists in `model_registry.py` or be `None`

### Requirement: is_cli_agent Naming Clarity

The `agent_runner.py` `_build_agent_hub_tool_guidance` function SHALL use the variable name `is_cli_agent` instead of `is_sdk_agent` for CLI adapter detection.

#### Scenario: CLI adapter detection

- **WHEN** `_build_agent_hub_tool_guidance` checks if the agent is a CLI adapter
- **THEN** the variable SHALL be named `is_cli_agent`
- **AND** the condition SHALL check `agent.adapter_name in ("claude-code", "codex")`

### Requirement: CodexAdapter SHALL use the Codex SDK

CodexAdapter MUST use `@openai/codex-sdk` `runStreamed()` rather than treating CLI spawn as the primary integration path.

#### Scenario: Codex run starts
- **WHEN** a Codex agent receives a prompt
- **THEN** the adapter starts or resumes a Codex thread
- **AND** translates thread, item, tool, and usage events into StreamEvent.

### Requirement: CodexAdapter SHALL expose AChat tools through MCP

CodexAdapter MUST expose allowlisted AChat tools through an adapter-owned MCP bridge. ClaudeCodeAdapter uses a dynamic MCP tool set (see the dynamic MCP tools requirement above).

#### Scenario: Codex creates and deploys an artifact or workspace build
- **WHEN** Codex calls the AChat MCP `write_artifact`, `deploy_artifact`, or `deploy_workspace` tool
- **THEN** the adapter translates the MCP result into `artifact.create` or `deploy.status`.

#### Scenario: CLI agent asks a structured user question
- **WHEN** Claude Code or Codex calls the AChat MCP `ask_user` tool
- **THEN** AChat routes it through the shared pending question flow.

### Requirement: Codex Base URL SHALL be Responses compatible

CodexAdapter MUST only accept Codex/Responses-compatible endpoints for `apiBaseUrl`; Chat Completions-only providers such as DeepSeek MUST be rejected or reported with a clear compatibility error.

#### Scenario: User configures DeepSeek for Codex
- **WHEN** `apiBaseUrl` points at `api.deepseek.com`
- **THEN** the adapter rejects the run before reconnect loops
- **AND** the error tells the user to use CustomAgentAdapter.

### Requirement: SDK runtime configuration SHALL be isolated

CodexAdapter MUST set `CODEX_HOME` and `CODEX_SQLITE_HOME` to AChat-managed data paths and strip unrelated external `CODEX_*` variables except certificate configuration.

#### Scenario: User has CC Switch configured locally
- **WHEN** AChat starts Codex SDK
- **THEN** the child runtime does not read the user's `~/.codex` config
- **AND** AChat per-agent settings determine key and base URL.

### Requirement: CLI adapters SHALL NOT receive model_id from build_adapter_input

CLI adapters (ClaudeCodeAdapter, CodexAdapter) MUST have `AdapterInput.model_id` set to `None` by `build_adapter_input`. CLI agents use their own local model configuration (CLI defaults or `--model` in custom_args which is blocked). The ModelProfile system applies only to SDK (Custom) agents.

#### Scenario: Claude Code agent run
- **WHEN** `build_adapter_input` is called for a `claude-code` adapter agent
- **THEN** the resulting `AdapterInput.model_id` is `None`
- **AND** no `--model` flag is passed to the CLI.

#### Scenario: Codex agent run
- **WHEN** `build_adapter_input` is called for a `codex` adapter agent
- **THEN** the resulting `AdapterInput.model_id` is `None`
- **AND** the Codex `thread/start` params include `model: None`.
