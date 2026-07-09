# Orchestrator

## Purpose

Defines the coordinated agent workflow for multi-agent task dispatch. The orchestrator is a regular agent that runs through the Unified Agent Loop (`run_agent_loop(mode='coordinated')`) with a `task_dispatch` tool. Detailed design lives in `specs/19-unified-agent-loop.md`.

## Requirements

### Requirement: Orchestrator SHALL be a normal Agent

The orchestrator MUST run through AgentRunner and `run_agent_loop` like any other agent; it SHALL not have a separate service path.

#### Scenario: User starts a group task
- **WHEN** the conversation `dispatch_mode` is `'orchestrated'`
- **AND** the active agent `is_orchestrator` is `True`
- **THEN** AgentRunner calls `run_agent_loop(mode='coordinated')`
- **AND** the orchestrator's tool list includes `task_dispatch`

### Requirement: Coordinated mode SHALL use task_dispatch for sub-agent dispatch

The orchestrator dispatches sub-tasks by calling the `task_dispatch` tool, which synchronously spawns a sub-agent loop and returns the result. There is no separate plan stage, plan approval, or DAG execution engine.

#### Scenario: Orchestrator dispatches a task
- **WHEN** the orchestrator calls `task_dispatch({ agentId, taskDescription })`
- **THEN** the handler calls `spawn_subagent_loop` to create a child run
- **AND** waits for the child run to complete
- **AND** returns `{ status, summary }` to the orchestrator's loop context

#### Scenario: Sub-agent fails
- **WHEN** the sub-agent run ends with an error
- **THEN** `task_dispatch` returns `{ status: 'failed', summary: error_text }`
- **AND** the orchestrator can choose to retry, re-dispatch, or report the failure

### Requirement: Non-orchestrator agents SHALL use solo mode

Agents in orchestrated conversations that are not the orchestrator run in solo mode. Only the orchestrator gets the `task_dispatch` tool.

#### Scenario: Regular agent in orchestrated conversation
- **WHEN** `dispatch_mode` is `'orchestrated'`
- **AND** `is_orchestrator` is `False`
- **THEN** AgentRunner calls `run_agent_loop(mode='solo')`
- **AND** the agent's tool list does NOT include `task_dispatch`

### Requirement: Solo conversations SHALL not use task_dispatch

Single-agent conversations (`dispatch_mode='solo'`) never inject `task_dispatch`, regardless of agent type.

#### Scenario: Solo conversation with orchestrator agent
- **WHEN** `dispatch_mode` is `'solo'`
- **AND** `is_orchestrator` is `True`
- **THEN** AgentRunner calls `run_agent_loop(mode='solo')`
- **AND** the agent's tool list does NOT include `task_dispatch`

### Requirement: Subagent runs SHALL use solo mode

When `task_dispatch` spawns a child run, the child always runs in solo mode via `execute_simple_run` directly (bypassing the dispatch mode routing).

#### Scenario: Subagent dispatch
- **WHEN** `spawn_subagent_loop` creates a `RunArgs` with `override_prompt`
- **THEN** `execute_run` detects `override_prompt` and calls `execute_simple_run` directly
- **AND** the sub-agent's tools are its own (no `task_dispatch`)

### Requirement: Aggregation SHALL be the orchestrator's natural end_turn

There is no separate aggregate stage. After dispatching tasks and receiving results, the orchestrator produces its final response as a natural `end_turn` text output.

#### Scenario: Orchestrator finishes after dispatch
- **WHEN** the orchestrator has received all `task_dispatch` results
- **THEN** it emits a final text response (end_turn)
- **AND** that response is the conversation's final message (no separate aggregate LLM call)

### Requirement: Legacy verification gates SHALL NOT exist

The old `plan_tasks` / `report_task_result` / verify-stage / retry-harness system has been removed. The orchestrator self-verifies via soft prompt guidance only.

#### Scenario: Agent completes a task
- **WHEN** any agent (solo or subagent) finishes its work
- **THEN** the run completes with the agent's `end_turn` text
- **AND** no verification gate, LLM judge, or retry harness runs
