# Task Dispatch Tool

## Purpose

Defines the `TaskDispatch` tool that the orchestrator agent uses to launch sub-agents within its own agent loop, analogous to Claude Code's Agent tool.

## Requirements

### Requirement: TaskDispatch tool SHALL launch a sub-agent and wait for completion

When the orchestrator calls `TaskDispatch`, the system SHALL synchronously run a sub-agent loop with the given agent and task description, then return the sub-agent's final text output to the orchestrator's loop context.

#### Scenario: Orchestrator dispatches a design task
- **WHEN** the orchestrator calls `TaskDispatch(agent_id="designer", task_description="Design the UI")`
- **THEN** a new solo agent loop starts with the designer agent and the task description
- **AND** the designer agent loop runs until `end_turn`
- **AND** the tool call returns with the designer's final text output.

#### Scenario: Sub-agent writes files during its loop
- **WHEN** a dispatched sub-agent writes files to the workspace
- **THEN** files are written to the same workspace as the parent conversation
- **AND** the orchestrator can read these files in subsequent loop iterations via `fs_read` / `fs_list`.

### Requirement: TaskDispatch SHALL only be available in orchestrated mode

The `TaskDispatch` tool SHALL only appear in the orchestrator's tool list when `dispatch_mode = 'orchestrated'`. Solo agents SHALL NOT have access to this tool.

#### Scenario: Orchestrator in group chat
- **WHEN** a conversation is in `orchestrated` mode
- **THEN** the orchestrator agent's tool list includes `TaskDispatch`.

#### Scenario: Solo agent conversation
- **WHEN** a conversation is in `solo` mode
- **THEN** `TaskDispatch` is NOT in the agent's tool list.

### Requirement: Sub-agents SHALL NOT dispatch further sub-agents

A sub-agent invoked via `TaskDispatch` SHALL NOT have `TaskDispatch` in its tool list. Only the top-level orchestrator can dispatch.

#### Scenario: Sub-agent attempts to dispatch another sub-agent
- **WHEN** a sub-agent's tool list is constructed
- **THEN** `TaskDispatch` SHALL NOT be included
- **AND** the sub-agent runs as a solo loop.

### Requirement: TaskDispatch SHALL fail fast if the target agent is unavailable

If the specified `agent_id` does not exist or is not available in the conversation, `TaskDispatch` SHALL return an error message in the tool result rather than blocking the orchestrator indefinitely.

#### Scenario: Target agent not found
- **WHEN** orchestrator calls `TaskDispatch(agent_id="nonexistent", task_description="...")`
- **THEN** the tool returns an error: `"Agent 'nonexistent' not found in conversation"`
- **AND** the orchestrator's loop continues; the model can choose an alternative.
