## Context

The orchestrator's dispatch evaluation pipeline (`harden-orchestrator-evidence-gates` + `add-dispatch-harness-loop`) applies uniform evidence gates to all code-implementation tasks: a successful `build/compile/test/lint/typecheck` command must appear in `RunToolEvidence.commands`. This works for工程项目 with a build toolchain but systematically rejects single-file tasks (e.g., a standalone HTML page) that have no `package.json` or equivalent manifest.

Three compounding issues cause the "agent reports complete → orchestrator silent → agent re-runs multiple times → eventually finishes" behavior observed in production:

1. **`report_task_result` is not a stream-terminal event** — unlike `plan_tasks` which has `on_tool_call → {"stop": True}`, the `report_task_result` tool result is only stored; the adapter feeds it back to the LLM, which continues generating.
2. **Evidence gates don't grade by workspace type** — `normalize_task_contract` auto-appends `CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION` / `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE` to any code task regardless of whether the workspace has a build toolchain.
3. **Failed commands are unrecoverable across variants** — `_has_later_successful_command` requires an exact command-string match; an agent that fixes a SyntaxError and reruns a shorter `python -c` variant does not recover the original failure.

## Goals / Non-Goals

**Goals:**
- Let single-file tasks (HTML/CSS/JS without a build toolchain) pass evidence gates when they provide file-write + deploy evidence.
- Stop agent stream immediately after `report_task_result` to prevent post-report busywork.
- Recover failed bash commands when a later command from the same tool family succeeds.
- Make harness-loop retries visible to the user via a `dispatch.retry` event.

**Non-Goals:**
- Changing the harness loop attempt count (`MAX_CHILD_TASK_ATTEMPTS = 4`).
- Changing the replan round count (`MAX_DISPATCH_ROUNDS = 4`).
- Adding new verification command patterns (e.g., treating `deploy_workspace` as verification).
- Redesigning the dispatch task card UI — only a small retry indicator is added.
- Changing the `plan_tasks` terminal-event behavior.
- Touching CLI adapters (claude-code, codex) — they manage their own tool loops.

## Decisions

### Decision 1: `report_task_result` terminal via `on_tool_call` callback

**Choice**: In `consume_stream`, when `require_task_report=True`, pass an `on_tool_call` callback that detects `report_task_result` tool calls and returns `{"stop": True, "result": {"acknowledged": True}}`, mirroring the `plan_tasks` pattern.

**Alternative considered**: Modify each adapter to stop after `report_task_result`. Rejected because adapters are platform-agnostic and shouldn't know about orchestration semantics; the terminal decision belongs to AgentRunner.

**Implementation**: `consume_stream` already has the `on_tool_call` parameter and the `{"stop": True}` break path (used by `plan_tasks` in `_run_plan_stage`). We reuse the same mechanism: when `require_task_report=True`, construct a callback that intercepts `report_task_result` and returns `stop=True`. The existing `tool.result` + `message.end` emission logic in the break path handles the rest.

### Decision 2: Workspace toolchain detection via manifest file presence

**Choice**: Add a pure function `workspace_has_build_toolchain(workspace_path: str) -> bool` that checks for the presence of known build manifests: `package.json`, `pom.xml`, `build.gradle` / `build.gradle.kts`, `Cargo.toml`, `go.mod`, `pyproject.toml`, `setup.py`, `Makefile`, `CMakeLists.txt`.

When the workspace has **no** build manifest:
- `normalize_task_contract` skips appending `CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION` and `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE`.
- `evaluate_task_result_report` skips the `has_successful_verification_command_evidence` gate (step ⑧).
- The `_required_evidence_satisfied` function returns `True` for `CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE` when the workspace has no toolchain.

**Alternative considered**: Let the Orchestrator LLM decide whether to declare `taskKind="code"`. Rejected because the LLM already declares code tasks correctly; the problem is the system's blanket evidence requirement, not the classification.

**Alternative considered**: Add a new `taskKind="single-file"` to distinguish. Rejected because it adds complexity to the plan schema and shifts burden to the LLM; workspace detection is deterministic and sufficient.

### Decision 3: Prefix-based failed-command recovery

**Choice**: Extend `_has_later_successful_command` to also check command-prefix matching. Extract the first token (or first two tokens for `python -c`, `pnpm run`, etc.) of the failed command; if any later command shares the same prefix and succeeded, the failed command is recovered.

```python
def _command_prefix(command: str) -> str:
    normalized = _normalize_command(command)
    parts = normalized.split()
    if len(parts) >= 2 and parts[0] in {"python", "python3", "pnpm", "npm", "yarn", "bun", "node"}:
        return " ".join(parts[:2])
    return parts[0] if parts else normalized
```

A failed command is recovered if any later command has the same `_command_prefix` AND `exit_code == 0` AND `not is_error` AND `not timed_out`.

**Alternative considered**: Recover if any later non-prepare command succeeded regardless of prefix. Rejected as too permissive — a `git status` success shouldn't recover a `pytest` failure.

**Alternative considered**: Fuzzy string matching (Levenshtein distance). Rejected — prefix matching is simpler, deterministic, and covers the observed pattern (agent fixes a SyntaxError and reruns a shorter variant of the same `python -c`).

### Decision 4: `dispatch.retry` event

**Choice**: Add a `DispatchRetryEvent` to `schemas/events.py` with fields: `conversation_id`, `timestamp`, `parent_run_id`, `task_id`, `attempt`, `max_attempts`, `error`. Publish it at the start of each harness-loop continuation (attempt 2+), before running the child task.

The event is informational — it does not change the dispatch DAG state. The frontend renders a small "↻ 重试 2/4" badge on the dispatch task card when it receives this event.

**Alternative considered**: Reuse `dispatch.end` with a `status="retrying"`. Rejected because `dispatch.end` semantics are terminal for a task; conflating retry with end would break downstream consumers.

## Risks / Trade-offs

- **[Risk] Workspace without manifest but actually needs build verification** → Mitigation: The `required_commands` declared by the Orchestrator LLM still gate completion regardless of workspace type. If the LLM declares `requiredCommands: [{command: "pnpm build"}]`, that gate still fires. Only the *auto-appended* code-task verification is skipped.

- **[Risk] Prefix matching too broad — `python -c "bad"` recovered by `python -c "good"` even when they test different things** → Mitigation: The recovery only prevents the failed command from *blocking* completion; it does not count as verification evidence. The code-task verification gate (when active) still requires a pattern-matched command. For workspaces without a toolchain, the verification gate is skipped anyway, so the prefix recovery is the only relevant check.

- **[Risk] `report_task_result` terminal cuts off legitimate post-report work** → Mitigation: The design intent is "report once, then stop." The sub-agent prompt already instructs "call report_task_result exactly once." If the agent needs to do cleanup before reporting, it should do so before calling the tool. The terminal behavior aligns with the existing instruction.

- **[Risk] `dispatch.retry` event not handled by older frontend** → Mitigation: The event is additive; older frontends ignore unknown event types. No breaking change to existing `dispatch.start` / `dispatch.end` events.
