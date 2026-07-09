# Solo Agent Loop

## Purpose

Defines the unified agent loop for single-chat scenarios, matching the Claude Code interaction paradigm: model-driven tool execution, self-verification within the loop, natural stop at `end_turn`.

## Requirements

### Requirement: Solo Agent Loop SHALL use a single model-driven while-loop

The system-agent tasks via one unified loop that continues until the model emits `end_turn`. No external verification gate, retry harness, or structured report tool SHALL block completion.

#### Scenario: User sends a task in single-chat
- **WHEN** a user sends a message in a conversation with `dispatch_mode = 'solo'`
- **THEN** AgentRunner invokes `run_agent_loop` with solo configuration
- **AND** the loop continues until the model emits `end_turn`
- **AND** the loop returns a `RunResult` with the model's final text output.

#### Scenario: Agent decides to run verification commands
- **WHEN** a model in solo loop decides to run tests, typecheck, or lint commands
- **THEN** the command is executed and the result is fed back into the loop context
- **AND** the model may choose to fix issues in subsequent loop iterations
- **AND** no external gate determines whether the verification passed.

#### Scenario: Agent finishes and returns
- **WHEN** the model emits `end_turn` after completing work
- **THEN** the loop stops immediately
- **AND** the model's final text is published as a normal message to the user
- **AND** no `report_task_result` is captured or evaluated.

### Requirement: Solo Agent Loop SHALL support all standard workspace tools

The tool list for a solo agent SHALL include `fs_read`, `fs_write`, `fs_list`, `fs_glob`, `fs_grep`, `bash`, `read_artifact`, `read_attachment`, and agent-specific tools. It SHALL NOT include `plan_tasks`, `report_task_result`, or `TaskDispatch`.

#### Scenario: Agent writes and reads files
- **WHEN** a solo model needs to read or write workspace files
- **THEN** standard file tools are available and function as today.

#### Scenario: Agent dispatches subagents
- **WHEN** a solo agent attempts to use `TaskDispatch`
- **THEN** the tool SHALL NOT be available in solo mode
- **AND** the model SHALL receive a tool-not-found error if attempted via prompt injection.

### Requirement: Solo mode SHALL be the default for new conversations

New conversations SHALL default to `dispatch_mode = 'solo'` unless the user or system explicitly selects orchestrated mode.

#### Scenario: New single-agent conversation is created
- **WHEN** a user creates a new conversation
- **THEN** `dispatch_mode` defaults to `'solo'`
- **AND** the agent starts in solo loop mode.

### Requirement: System prompt SHALL encourage self-verification without hard gate

The system prompt for solo mode SHALL include a soft suggestion to verify work before finishing (e.g., run typecheck/tests), but SHALL NOT require it as a blocking gate.

#### Scenario: Agent finishes writing code
- **WHEN** a solo agent finishes implementing a feature
- **THEN** the system prompt reminds the user to verify
- **BUT** the agent CAN end without verification if it chooses to.
