# Spec Delta: Lifecycle Hooks

## ADDED Requirements

### Requirement: Hooks SHALL support on_task_verified event

The system MUST support an `on_task_verified` hook event type, dispatched after the Verify stage checks a task result. The hook context MUST include the task id, task kind, verification result, and failure reason (if any).

#### Scenario: on_task_verified dispatched after verification

- **WHEN** the Verify stage completes checking a task result
- **THEN** `on_task_verified` is dispatched with `task_id`, `task_kind`, `verification_result` (`passed` or `failed`), `failure_reason` (if failed)
- **AND** the hook result is processed (allow/deny/modify/inject).

#### Scenario: Custom verification via on_task_verified hook

- **WHEN** an `on_task_verified` handler returns `HookResult(action="modify", data={"verification_result": "failed", "failure_reason": "custom check failed"})`
- **THEN** the task's verification result is overridden to `failed`
- **AND** the custom failure reason is included in the replan context.
