## Why

Dispatched child tasks that produce standalone files (e.g., a single HTML page) are systematically rejected by the evidence gate system. The gate requires a successful `build/compile/test/lint/typecheck` command (`exitCode=0`) tracked in `RunToolEvidence.commands`, but single-file tasks have no build toolchain and naturally cannot satisfy this. Additionally, `report_task_result` is not a stream-terminal event — unlike `plan_tasks`, the adapter feeds the tool result back to the LLM, causing the agent to continue generating after reporting. Finally, failed bash commands from agent trial-and-error are not recoverable unless the exact same command string is later retried successfully, which rarely happens when the agent fixes the command and reruns a different variant.

## What Changes

- **report_task_result as terminal event**: When `require_task_report=True` and `consume_stream` detects a `tool.result` for `report_task_result`, it MUST stop consuming the stream (emit `tool.result` + `message.end`, then `break`) — mirroring the `plan_tasks` terminal behavior. This prevents the agent from continuing to generate after reporting completion.
- **Workspace-aware evidence grading**: The code-task verification command gate (`has_successful_verification_command_evidence`) and the auto-appended `CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION` / `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE` MUST be skipped when the workspace has no build toolchain manifest (`package.json`, `pom.xml`, `build.gradle`, `Cargo.toml`, `go.mod`, `pyproject.toml`). For such workspaces, file-write evidence + deploy success suffices.
- **Semantic failed-command recovery**: The `_has_later_successful_command` check MUST consider command-prefix matching (e.g., two `python -c` calls share a prefix) so that a failed trial command is recovered by a later successful variant of the same tool family, not just by an exact string match.
- **Harness loop transparency**: When the harness loop retries a child task, AgentRunner MUST publish a `dispatch.retry` event containing the attempt number, the failure reason, and the evaluation error, so the frontend can show retry status instead of silently re-running the agent.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `orchestrator`: Evidence gate requirements now grade by workspace toolchain presence; `report_task_result` is a stream-terminal event for child task runs; failed bash commands are recoverable via prefix matching; harness loop retries emit `dispatch.retry` events.

## Impact

- **Backend `agent_runner.py`**: `consume_stream` gains terminal-event logic for `report_task_result` when `require_task_report=True`.
- **Backend `orchestrator.py`**: `_run_child_task` publishes `dispatch.retry` events; `_evaluate_child_task_result` passes workspace context to the evaluation.
- **Backend `task_result_report.py`**: `evaluate_task_result_report` skips code-task verification gate when workspace has no build manifest; `_has_later_successful_command` gains prefix-matching recovery.
- **Backend `dispatch_plan.py`**: `normalize_task_contract` skips auto-appending `CODE_TASK_RUNNABLE_*` when workspace has no build manifest.
- **Backend `schemas/events.py`**: New `DispatchRetryEvent` type.
- **Frontend**: Dispatch task card renders retry indicator when `dispatch.retry` events arrive.
- **Tests**: New test cases for terminal-event behavior, workspace-aware grading, prefix recovery, and retry event emission.
