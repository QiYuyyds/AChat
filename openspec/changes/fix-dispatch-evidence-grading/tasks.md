## 1. Workspace toolchain detection

- [x] 1.1 Add `workspace_has_build_toolchain(workspace_path: str) -> bool` pure function in `app/utils/workspace_utils.py` that checks for `package.json`, `pom.xml`, `build.gradle`, `build.gradle.kts`, `Cargo.toml`, `go.mod`, `pyproject.toml`, `setup.py`, `Makefile`, `CMakeLists.txt` in the workspace root.
- [x] 1.2 Add unit tests in `tests/test_workspace_utils.py` covering: workspace with `package.json` → True; workspace with only `index.html` → False; workspace with `pyproject.toml` → True; empty workspace → False.

## 2. Evidence gate grading by workspace type

- [x] 2.1 Modify `normalize_task_contract` in `app/services/dispatch_plan.py` to accept a `has_build_toolchain: bool` parameter; when False, skip appending `CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION` and `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE`.
- [x] 2.2 Thread `has_build_toolchain` through `compile_and_validate_dispatch_plan` → `normalize_task_contract` call sites in `orchestrator.py` (`_run_plan_stage` and the replan path); resolve workspace path and call `workspace_has_build_toolchain` before plan compilation.
- [x] 2.3 Modify `evaluate_task_result_report` in `app/services/task_result_report.py` to accept a `has_build_toolchain: bool` parameter; when False, skip the `has_successful_verification_command_evidence` gate (step ⑧) and return `True` from `_required_evidence_satisfied` for `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE`.
- [x] 2.4 Thread `has_build_toolchain` through `_evaluate_child_task_result` → `evaluate_task_result_report` in `orchestrator.py`; resolve workspace from `ctx.workspace` and pass the flag.
- [x] 2.5 Update existing tests in `tests/test_dispatch_plan.py` and `tests/test_task_result_evaluate.py` to cover: code task without toolchain → no auto-appended criteria; code task without toolchain → verification gate skipped; code task with toolchain → existing behavior unchanged.

## 3. report_task_result terminal event

- [x] 3.1 In `app/services/agent_runner.py` `consume_stream`, when `require_task_report=True`, construct an `on_tool_call` callback that detects `report_task_result` (using `extract_plan_tasks_tool_args`-style name matching or direct `REPORT_TASK_RESULT_TOOL_NAME` check) and returns `{"stop": True, "result": {"acknowledged": True}}`.
- [x] 3.2 Verify the existing `{"stop": True}` break path in `consume_stream` correctly emits `tool.result` + `message.end` for this callback (same path used by `plan_tasks`).
- [x] 3.3 Ensure `task_report` is still captured from the `tool.result` event before the break (existing `tool.result` handling runs before `on_tool_call` break check in the event loop — verify ordering).
- [x] 3.4 Add test in `tests/test_custom_adapter.py` or `tests/test_agent_runner.py`: child run with `require_task_report=True` → adapter emits `report_task_result` tool call → stream stops, no second LLM turn, `task_report` is set.

## 4. Failed command prefix recovery

- [x] 4.1 Add `_command_prefix(command: str) -> str` helper in `app/services/task_result_report.py` that extracts the first token (or first two tokens for `python`/`python3`/`pnpm`/`npm`/`yarn`/`bun`/`node`).
- [x] 4.2 Extend `_has_later_successful_command` to check prefix matching: if any later command has the same `_command_prefix` AND `exit_code == 0` AND `not is_error` AND `not timed_out`, return True.
- [x] 4.3 Add tests in `tests/test_task_result_evaluate.py`: failed `python -c "bad"` recovered by later `python -c "good"` → not in `failed_commands`; failed `pytest` not recovered by later `pnpm build` → still in `failed_commands`.

## 5. dispatch.retry event

- [x] 5.1 Add `DispatchRetryEvent` dataclass in `app/schemas/events.py` with fields: `conversation_id`, `timestamp`, `parent_run_id`, `task_id`, `attempt`, `max_attempts`, `error`; set `type = "dispatch.retry"`.
- [x] 5.2 In `app/services/orchestrator.py` `_run_child_task`, before launching continuation attempts (attempt 2+), publish a `DispatchRetryEvent` with the current attempt number, `MAX_CHILD_TASK_ATTEMPTS`, and the evaluation error from the previous attempt.
- [x] 5.3 Add test in `tests/test_orchestrator.py`: child task that fails first attempt and retries → `dispatch.retry` event emitted with correct `attempt` and `error` fields before second `dispatch.start`.

## 6. Frontend retry indicator

- [x] 6.1 Add `dispatch.retry` event type to the shared event types in `src/shared/types.ts` (or equivalent).
- [x] 6.2 In the dispatch task card component (likely `src/components/dispatch/`), handle `dispatch.retry` events: render a "↻ 重试 {attempt}/{max_attempts}" badge with the error tooltip.
- [x] 6.3 Verify the retry badge appears during harness-loop retries and disappears when `dispatch.end` arrives.

## 7. Integration verification

- [x] 7.1 End-to-end manual test: create a group chat with an Orchestrator + a custom agent; send "生成一个贪吃蛇HTML小游戏"; verify the child task completes on first attempt (no harness loop retry) when the agent provides file-write + deploy evidence.
- [x] 7.2 End-to-end manual test: same setup with a Node.js project workspace (`package.json` present); verify code-task verification gate still requires `pnpm build` / `tsc` evidence.
- [x] 7.3 Regression check: existing orchestrator tests pass (`pytest tests/test_orchestrator.py tests/test_dispatch_plan.py tests/test_task_result_evaluate.py`).
