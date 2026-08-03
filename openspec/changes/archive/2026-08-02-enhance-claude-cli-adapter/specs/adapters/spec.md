## MODIFIED Requirements

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

## REMOVED Requirements

### Requirement: ClaudeCodeAdapter SHALL auto-approve all control requests

~~The adapter auto-responds `allow` to all `control_request` events in autonomous mode.~~

**Reason**: Replaced by smart approval routing. The adapter now parses `control_request` events and routes them through AChat's security infrastructure (blacklist, approval gate, pending writes, path sandbox) before responding.

**Migration**: The `_auto_approve` method is replaced by `_handle_control_request` which implements the routing logic. The `--permission-mode` changes from `bypassPermissions` to `acceptEdits`.
