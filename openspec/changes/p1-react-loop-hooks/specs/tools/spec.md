# Spec Delta: Tools

## ADDED Requirements

### Requirement: ToolExecutor SHALL support hook-aware execution

`ToolExecutor` MUST provide an `execute_with_hooks(name, args, ctx, hook_registry)` method that dispatches `pre_tool_use` and `post_tool_use` hooks around tool execution. The method SHALL respect `deny`, `modify`, and `allow` hook results.

#### Scenario: pre_tool_use hook denies execution

- **WHEN** `execute_with_hooks` is called for `bash` with args `{"command": "rm -rf /"}`
- **AND** a `pre_tool_use` hook returns `HookResult(action="deny", data="command blocked by blacklist")`
- **THEN** the tool is NOT executed
- **AND** a `ToolResult` with `ok=False` and the deny reason is returned
- **AND** a `tool.result` event with `is_error=True` is emitted.

#### Scenario: pre_tool_use hook modifies arguments

- **WHEN** a `pre_tool_use` hook returns `HookResult(action="modify", data={"args": modified_args})`
- **THEN** the tool is executed with `modified_args` instead of the original `args`.

#### Scenario: post_tool_use hook modifies result

- **WHEN** a `post_tool_use` hook returns `HookResult(action="modify", data={"result": modified_result})`
- **THEN** the `ToolResult.value` is replaced with `modified_result`
- **AND** the modified result is used in the `tool.result` event and injected into `messages`.

#### Scenario: No hooks registered

- **WHEN** `execute_with_hooks` is called with no hooks registered for `pre_tool_use` or `post_tool_use`
- **THEN` the tool is executed normally
- **AND** the result is returned as-is.

### Requirement: SDK tool execution SHALL be managed by AgentRunner

For SDK adapter runs using `call_once`, tool execution MUST be performed by AgentRunner in the ReAct loop, not by the adapter. AgentRunner SHALL execute tools via `execute_with_hooks` and inject results into the `messages` list for the next turn.

#### Scenario: AgentRunner executes tools between turns

- **WHEN** `call_once` yields `tool.call` events
- **THEN** AgentRunner parses the tool calls from the turn result
- **AND** executes each tool via `execute_with_hooks`
- **AND** emits `tool.result` events
- **AND** appends `{"role": "tool", "tool_call_id": ..., "content": ...}` to `messages`
- **AND** continues to the next `call_once` turn.

#### Scenario: Tool execution in review mode

- **WHEN** the workspace is in review mode
- **AND** a `fs_write` tool call is received
- **THEN** the `tool_approval` hook intercepts the call via `pre_tool_use`
- **AND** returns `HookResult(action="deny")` with a pending-approval message
- **AND** the tool result indicates a pending approval is required.

## MODIFIED Requirements

### Requirement: File tools SHALL enforce workspace boundaries

`fs_read`, `fs_write`, and `bash` MUST resolve paths under the conversation effective cwd and reject access outside that tree. When executed via `execute_with_hooks`, path validation SHALL occur in the tool handler, not in the hook layer.

#### Scenario: Agent attempts path traversal

- **WHEN** a tool receives `../../.ssh/id_rsa`
- **THEN** the path check rejects the operation
- **AND** the `ToolResult` indicates an error.

### Requirement: Bash SHALL enforce platform blacklist

The bash tool MUST reject commands that match the platform-specific banned pattern list before execution. When executed via `execute_with_hooks`, the blacklist check MAY be implemented as a `pre_tool_use` hook (`tool_approval`) or in the tool handler directly.

#### Scenario: POSIX destructive command is requested

- **WHEN** the command matches `rm -rf /`
- **THEN** the tool refuses to run it
- **AND** the `ToolResult` indicates the command was blocked.

#### Scenario: POSIX background process inherits stdio

- **WHEN** a bash command starts a background process and the shell script exits
- **THEN** the bash tool MUST NOT wait forever on inherited stdout or stderr
- **AND** it SHOULD clean up the command process group before returning.

### Requirement: Key bash commands SHALL require user approval

AChat MUST require explicit user approval before executing bash commands that are not banned but can materially change dependencies, discard files, or affect host-level runtime state. This approval gate MUST be implemented as a `pre_tool_use` hook when running via `execute_with_hooks`, and as an inline check in the legacy `stream` path.

#### Scenario: Agent installs dependencies

- **WHEN** an agent requests `pnpm install` via the ReAct loop
- **THEN** the `tool_approval` hook records a pending bash command
- **AND** emits it through the conversation event stream
- **AND** the tool is executed only after user approval.

#### Scenario: Agent installs dependencies via legacy stream path

- **WHEN** an agent requests `pnpm install` via the `stream` path
- **THEN** AChat records a pending bash command inline
- **AND** emits it through the conversation event stream
- **AND** executes the command only after user approval.
