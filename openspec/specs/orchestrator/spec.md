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

The orchestrator dispatches sub-tasks by calling the `task_dispatch` tool, which synchronously spawns a sub-agent loop and returns the result. When the workspace is a git repository (sandbox mode auto-initializes git), `task_dispatch` SHALL create a worktree for the sub-agent, merge-back after completion, and clean up the worktree. If worktree creation fails, the dispatch SHALL degrade to shared-workspace mode (current behavior).

#### Scenario: Orchestrator dispatches a task with worktree isolation

- **WHEN** the orchestrator calls `task_dispatch({ taskDescription })`
- **AND** the workspace is a git repository
- **THEN** the handler calls `create_worktree()` to create an isolated worktree
- **AND** passes the worktree path as `override_workspace_path` to `spawn_subagent_loop`
- **AND** after the sub-agent completes, calls `merge_worktree_back()`
- **AND** calls `cleanup_worktree()` regardless of merge success
- **AND** returns `{ status, summary }` to the orchestrator's loop context

#### Scenario: Worktree creation fails, degrades to shared workspace

- **WHEN** `create_worktree()` returns `None`
- **THEN** the handler proceeds without worktree isolation
- **AND** `spawn_subagent_loop` is called without `override_workspace_path`
- **AND** no merge-back is performed after completion

#### Scenario: Sub-agent fails after worktree creation

- **WHEN** the sub-agent run ends with an error
- **AND** a worktree was created
- **THEN** `merge_worktree_back()` is still called (to preserve partial work)
- **AND** `cleanup_worktree()` is called after merge-back
- **AND** `task_dispatch` returns `{ status: 'failed', summary: error_text }`

#### Scenario: Sub-agent fails without worktree (unchanged)

- **WHEN** the sub-agent run ends with an error
- **AND** no worktree was created (non-git workspace or creation failed)
- **THEN** `task_dispatch` returns `{ status: 'failed', summary: error_text }`
- **AND** the orchestrator can choose to retry, re-dispatch, or report the failure

### Requirement: DAG executor SHALL create worktrees for parallel wave tasks

When `dag_executor.execute_dag()` executes a wave of parallel tasks, each ready task SHALL get its own worktree if the workspace is a git repository. After each task completes, its worktree SHALL be merged back before the next wave begins. Failed merges with conflicts SHALL be resolved via the three-layer conflict resolution strategy before proceeding.

#### Scenario: DAG wave with worktree isolation

- **WHEN** `execute_dag()` starts a wave with 3 ready tasks
- **AND** the workspace is a git repository
- **THEN** each task gets its own worktree via `create_worktree()`
- **AND** tasks execute in parallel in their isolated worktrees
- **AND** as each task completes, `merge_worktree_back()` is called
- **AND** `cleanup_worktree()` is called after each merge-back

#### Scenario: Merge conflict blocks next wave

- **WHEN** a task's `merge_worktree_back()` encounters a conflict
- **AND** the conflict reaches Layer 3 (human approval)
- **THEN** the DAG executor blocks on the merge-back completion
- **AND** the next wave does not start until the conflict is resolved
- **AND** other tasks in the current wave that haven't merged yet continue independently

#### Scenario: Worktree creation fails for one task in a wave

- **WHEN** `create_worktree()` fails for one task in a wave
- **THEN** that task degrades to shared-workspace mode
- **AND** other tasks in the wave that successfully created worktrees are unaffected
- **AND** the degraded task does not call `merge_worktree_back()` after completion

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
