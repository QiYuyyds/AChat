# Orchestrator

## MODIFIED Requirements

### Requirement: Non-orchestrator agents SHALL use solo mode

Agents in orchestrated conversations that are not the orchestrator run in solo mode. Solo agents now receive `task_dispatch` (subject to dispatch depth limit) so they can clone themselves for subtasks.

#### Scenario: Regular agent in orchestrated conversation
- **WHEN** `dispatch_mode` is `'orchestrated'`
- **AND** `is_orchestrator` is `False`
- **THEN** AgentRunner calls `run_agent_loop(mode='solo')`
- **AND** the agent's tool list includes `task_dispatch` when `dispatch_depth < MAX_DISPATCH_DEPTH`

### Requirement: Solo conversations SHALL support task_dispatch

Single-agent conversations (`dispatch_mode='solo'`) now inject `task_dispatch` when the dispatch depth is below the maximum, allowing solo agents to clone themselves for subtasks.

#### Scenario: Solo conversation agent dispatches a subtask
- **WHEN** `dispatch_mode` is `'solo'`
- **AND** `dispatch_depth` is less than `MAX_DISPATCH_DEPTH`
- **THEN** the agent's tool list includes `task_dispatch`
- **AND** calling `task_dispatch` without `agentId` clones the calling agent

#### Scenario: Solo conversation at max depth
- **WHEN** `dispatch_mode` is `'solo'`
- **AND** `dispatch_depth` equals `MAX_DISPATCH_DEPTH`
- **THEN** the agent's tool list does NOT include `task_dispatch`

### Requirement: Subagent runs SHALL use subagent mode

When `task_dispatch` spawns a child run, the child runs through `run_agent_loop(mode='subagent')` instead of `execute_simple_run` directly. Subagent mode injects `task_dispatch` (subject to depth limit) so subagents can recursively dispatch.

#### Scenario: Subagent dispatch with recursion
- **WHEN** `spawn_subagent_loop` creates a `RunArgs` with `override_prompt`
- **AND** `dispatch_depth` is less than `MAX_DISPATCH_DEPTH`
- **THEN** `execute_run` calls `run_agent_loop(mode='subagent')`
- **AND** the subagent's tool list includes `task_dispatch`

#### Scenario: Subagent at max depth
- **WHEN** `dispatch_depth` equals `MAX_DISPATCH_DEPTH`
- **THEN** the subagent's tool list does NOT include `task_dispatch`
- **AND** the subagent acts as a terminal executor

### Requirement: Coordinated mode SHALL use task_dispatch for sub-agent dispatch

The orchestrator dispatches sub-tasks by calling the `task_dispatch` tool, which synchronously spawns a sub-agent loop and returns the result. The `agentId` parameter is optional: when omitted, the calling agent clones itself; when specified, it dispatches to a group member.

#### Scenario: Orchestrator dispatches to a group member
- **WHEN** the orchestrator calls `task_dispatch({ agentId, taskDescription })`
- **AND** `agentId` is a group member
- **THEN** the handler calls `spawn_subagent_loop` with `dispatch_visibility='visible'`
- **AND** the child run's messages are visible in conversation history

#### Scenario: Orchestrator dispatches without agentId (clone)
- **WHEN** the orchestrator calls `task_dispatch({ taskDescription })` without `agentId`
- **THEN** the handler uses the orchestrator's own `agent_id`
- **AND** calls `spawn_subagent_loop` with `dispatch_visibility='hidden'`
- **AND** the child run's messages are hidden from conversation history

### Requirement: Subagent runs SHALL only clone themselves

Subagent runs (non-coordinated mode) can only clone themselves via `task_dispatch`. They cannot dispatch to other group members, preventing dispatch cycles.

#### Scenario: Subagent attempts to dispatch to another agent
- **WHEN** a subagent calls `task_dispatch({ agentId: 'other-agent-id' })`
- **AND** `agentId` differs from the calling agent's `agent_id`
- **THEN** the tool returns an error: "Subagent can only clone itself; cannot dispatch to other agents"

#### Scenario: Subagent clones itself
- **WHEN** a subagent calls `task_dispatch({ taskDescription })` without `agentId`
- **THEN** the handler uses the subagent's own `agent_id`
- **AND** spawns a child run with `dispatch_depth + 1` and `dispatch_visibility='hidden'`
