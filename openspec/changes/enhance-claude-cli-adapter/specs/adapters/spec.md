# specs/adapters/spec.md

## Requirements

### Requirement: ClaudeCodeAdapter CLI Execution

The `ClaudeCodeAdapter` SHALL execute the `claude` binary as a subprocess using the `--print --output-format stream-json --input-format stream-json --include-partial-messages --verbose` flags. The adapter SHALL NOT use the Claude Code SDK or any Python SDK to communicate with the model.

#### Scenario: Basic CLI invocation

- **WHEN** `run()` is called with a valid `adapter_input`
- **THEN** the adapter SHALL construct CLI args from `adapter_input` and spawn the `claude` process
- **AND** SHALL write the prompt via stdin in stream-json format
- **AND** SHALL read events from stdout until the process exits

#### Scenario: Custom args override

- **WHEN** `adapter_input.custom_args` is provided
- **THEN** the adapter SHALL merge them after built-in args
- **AND** SHALL reject any args in `_claude_blocked_args` (including `--disallowedTools`, `--model`, `--permission-mode`, `--allowedTools`, `--resume`, `--session-id`, `--debug`)
- **AND** SHALL emit a `RunErrorEvent` with a clear message if a blocked arg is detected

### Requirement: Session Resume via DB Persistence

The `ClaudeCodeAdapter` SHALL support multi-turn session resume by persisting the Claude Code `session_id` in the `AgentRun.cli_session_id` DB column and passing `--resume <session_id>` on subsequent runs.

#### Scenario: First run (no prior session)

- **WHEN** there is no `cli_session_id` for the conversation + agent (no prior CLI run, or prior runs have NULL `cli_session_id`)
- **THEN** the adapter SHALL start a fresh Claude Code session (no `--resume` flag)
- **AND** SHALL capture `session_id` from the result event
- **AND** the `agent_runner` SHALL persist it to `AgentRun.cli_session_id` after the run

#### Scenario: Subsequent run (resume)

- **WHEN** `build_adapter_input` finds a `cli_session_id` (from in-memory cache or DB query on the latest matching `AgentRun`)
- **THEN** the adapter SHALL pass `--resume <session_id>` in CLI args
- **AND** SHALL capture the (possibly new) `session_id` from the result event
- **AND** the `agent_runner` SHALL persist the new `cli_session_id` to `AgentRun`

#### Scenario: Session resume failure

- **WHEN** `--resume` is passed but the CLI exits with an error indicating the session is invalid or expired
- **THEN** the adapter SHALL retry without `--resume` (fresh session)
- **AND** SHALL capture the new `session_id` from the fresh run's result event
- **AND** SHALL emit a warning event

#### Scenario: Cache miss with DB fallback

- **WHEN** the in-memory `session_store` has no entry for `conversation_id:agent_id`
- **THEN** `build_adapter_input` SHALL query the latest `AgentRun` for the same `conversation_id` + `agent_id` where `cli_session_id IS NOT NULL`, ordered by `started_at DESC`
- **AND** SHALL use the result as the `cli_resume_session_id`
- **AND** SHALL populate the in-memory cache for subsequent reads

#### Scenario: Session cache invalidation

- **WHEN** a conversation is deleted, cleared, withdrawn, or regenerated
- **THEN** the `session_store` SHALL clear the in-memory cache entry for that conversation
- **AND** the DB column on old runs SHALL remain unchanged (historical record)

#### Scenario: Backend restart

- **WHEN** the backend restarts (in-memory cache is empty)
- **AND** a new run is started for an existing conversation with a prior CLI run
- **THEN** `build_adapter_input` SHALL query the DB for the latest `cli_session_id`
- **AND** SHALL pass `--resume` to resume the session

### Requirement: Smart Approval via control_request Routing

The `ClaudeCodeAdapter` SHALL use `--permission-mode acceptEdits` and route `control_request` events through AChat's security infrastructure instead of blindly auto-approving.

#### Scenario: Bash command control_request

- **WHEN** a `control_request` event is received for a Bash tool call
- **THEN** the adapter SHALL extract the command string
- **AND** SHALL call `find_banned_pattern` to check against the platform-appropriate blacklist
- **AND** SHALL call `classify_bash_approval` to determine the approval class
- **AND** SHALL call `wait_for_bash_approval` to get the user's decision (in review mode) or auto-decide (in trust mode)
- **AND** SHALL respond with `behavior=allow` or `behavior=deny` accordingly

#### Scenario: File write control_request

- **WHEN** a `control_request` event is received for a Write/Edit tool call
- **THEN** the adapter SHALL call `resolve_safe_path` to verify the path is within the workspace sandbox
- **AND** SHALL call `pending_writes.register` to queue the write for review
- **AND** SHALL respond with `behavior=allow` (defer the actual decision to the pending write flow) or `behavior=deny` (if path is outside sandbox)

#### Scenario: Banned command detected

- **WHEN** `find_banned_pattern` returns a match
- **THEN** the adapter SHALL respond with `behavior=deny` immediately
- **AND** SHALL emit a `ToolApprovalEvent` with `decision=deny` and the matched pattern

#### Scenario: Path outside workspace

- **WHEN** `resolve_safe_path` raises a path violation
- **THEN** the adapter SHALL respond with `behavior=deny`
- **AND** SHALL emit a `ToolApprovalEvent` with `decision=deny` and the violation detail

### Requirement: Attachment Support

The `ClaudeCodeAdapter` SHALL support image and file attachments by encoding them into stream-json content blocks.

#### Scenario: Image attachment

- **WHEN** `adapter_input.attachments` contains an entry with `mime_type` starting with `image/`
- **THEN** `_write_prompt` SHALL add an `{"type": "image", "source": {"type": "base64", "media_type": "<mime>", "data": "<base64>"}}` content block to the user message
- **AND** SHALL reject images larger than 10 MB with a clear error

#### Scenario: File attachment (non-image)

- **WHEN** `adapter_input.attachments` contains a non-image entry
- **THEN** `_write_prompt` SHALL append a text note to the prompt: "Attached file: <fileName> (<mimeType>) at <absPath>"
- **AND** Claude Code MAY use its built-in Read tool to access the file

### Requirement: Dynamic MCP Tool Exposure

The `ClaudeCodeAdapter` SHALL pass the agent's configured `tool_names` to the MCP Bridge process so that only configured tools are exposed.

#### Scenario: Agent with MCP tools configured

- **WHEN** an agent has `tool_names = ["web_search", "rag_search"]`
- **THEN** the adapter SHALL pass `--tool-names web_search,rag_search` to the MCP Bridge subprocess
- **AND** the MCP Bridge SHALL only register and expose those tools
- **AND** the `ACHAT_MCP_TOOL_HINT` system prompt SHALL be dynamically generated from the actual exposed tool list

#### Scenario: Agent with no MCP tools

- **WHEN** an agent has an empty `tool_names`
- **THEN** the MCP Bridge SHALL expose no tools
- **AND** the `ACHAT_MCP_TOOL_HINT` SHALL be empty

### Requirement: MCP Bridge Event Loop Reuse

The MCP Bridge SHALL use a single module-level asyncio event loop for all tool executions instead of creating a new loop per call.

#### Scenario: First tool call

- **WHEN** `_execute_tool` is called for the first time
- **THEN** the MCP Bridge SHALL create a module-level event loop and store it
- **AND** SHALL run the async tool handler on that loop

#### Scenario: Subsequent tool calls

- **WHEN** `_execute_tool` is called again
- **THEN** the MCP Bridge SHALL reuse the existing module-level event loop
- **AND** SHALL NOT create a new `asyncio.new_event_loop()`

### Requirement: Timeout Watchdog

The `ClaudeCodeAdapter` SHALL enforce inactivity and first-turn timeouts to prevent indefinite hangs.

#### Scenario: Semantic inactivity timeout

- **WHEN** no meaningful event (text, tool_use, tool_result, result) is received for `semantic_inactivity_timeout` (default 10 minutes)
- **THEN** the adapter SHALL emit a `RunErrorEvent` with timeout details
- **AND** SHALL terminate the CLI process

#### Scenario: First-turn no-progress timeout

- **WHEN** no meaningful event is received within `first_turn_no_progress_timeout` (default 30 seconds) of the run starting
- **THEN** the adapter SHALL emit a `RunErrorEvent` with timeout details
- **AND** SHALL terminate the CLI process

### Requirement: Blocked Args Hardening

The `ClaudeCodeAdapter` SHALL block `--disallowedTools` and `--model` in custom args to prevent security-critical overrides.

#### Scenario: User passes --disallowedTools via custom_args

- **WHEN** `custom_args` contains `--disallowedTools`
- **THEN** the adapter SHALL reject it and emit a `RunErrorEvent` explaining that `--disallowedTools` is managed by the system

#### Scenario: User passes --model via custom_args

- **WHEN** `custom_args` contains `--model`
- **THEN** the adapter SHALL reject it and emit a `RunErrorEvent` explaining that `--model` is managed by the system

### Requirement: Dead Code Cleanup

The `ClaudeCodeAdapter` SHALL remove the unused `output_parts` accumulator and fix `DEFAULT_CLAUDE_MODEL` to reference a valid model identifier.

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
- **AND** the branch SHALL be dead code for CLI agents (the function is only called when `is_sdk=True`, i.e., `adapter_name in SDK_ADAPTERS = {"custom"}`)
