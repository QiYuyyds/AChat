# Implementation Tasks: Unified Agent Loop

## 1. DB Schema + Conversation Model

- [x] 1.1 Add `dispatch_mode` column to `conversations` table (VARCHAR, default `'solo'`, nullable for backward compat)
- [x] 1.2 Update `Conversation` SQLAlchemy model in `backend/app/db/models.py`
- [x] 1.3 Update Pydantic schema in `backend/app/schemas/` (if separate from model)
- [x] 1.4 Generate and run Alembic migration
- [x] 1.5 Verify existing queries don't break (defensive `getattr(conv, 'dispatch_mode', 'solo')`)

## 2. Core Agent Loop Abstraction

- [x] 2.1 Create `backend/app/services/agent_loop.py` with `run_agent_loop` async function
- [x] 2.2 Define `AgentLoopConfig` dataclass: `mode: 'solo' | 'coordinated' | 'subagent'`, `conversation_id`, `trigger_message_id`, `cancel_event`
- [x] 2.3 Define `RunResult` dataclass: `status`, `text`, `artifact_ids`, `output_message_ids`
- [x] 2.4 Implement the single while-loop: model call → tool execution → check `end_turn` → break
- [x] 2.5 Implement event publishing within the loop (same `publish()` calls today)
- [x] 2.6 Handle cancel event (user abort) as immediate stop with partial result

## 3. Solo Agent Mode

- [x] 3.1 Update `agent_runner.execute_run` to check `conversation.dispatch_mode`
- [x] 3.2 Wire `'solo'` → `run_agent_loop(mode='solo')`
- [x] 3.3 Ensure solo mode uses agent's own tools (no `TaskDispatch`, no `report_task_result`, no `plan_tasks`)
- [x] 3.4 Update system prompt builder to inject soft self-verify reminder in solo mode
- [x] 3.5 Solo mode returns model's `end_turn` text as the message content (no summary wrap)
- [x] 3.6 End-to-end test: solo mode with a file-write task → no extra LLM calls, no gate states

## 4. TaskDispatch Tool

- [x] 4.1 Create `backend/app/tools/task_dispatch.py` with `TaskDispatchTool` definition
- [x] 4.2 Tool name: `task_dispatch`; parameters: `agent_id` (string), `task_description` (string), `depends_on` (optional string[])
- [x] 4.3 Handler: synchronously call `run_agent_loop(mode='subagent', agent=agent, initial_message=task_description)`
- [x] 4.4 Handler: on sub-agent `end_turn`, return `{ status: 'completed', summary: text }` as tool result
- [x] 4.5 Handler: on error/not-found, return error text as tool result (not exception)
- [x] 4.6 Register tool in `tool_registry` only when `mode = 'coordinated'`

## 5. Coordinated (Orchestrator) Mode

- [x] 5.1 Wire `'orchestrated'` → `run_agent_loop(mode='coordinated')`
- [x] 5.2 Coordinated agent's tool list = agent's tools + `TaskDispatch`
- [x] 5.3 Coordinated agent's system prompt includes soft dispatch guidance ("dispatch when you lack capability, do it yourself when you can")
- [x] 5.4 Aggregate phase becomes orchestrator's natural `end_turn` text output (no separate XML/template)
- [x] 5.5 End-to-end test: group with 2 agents → orchestrator dispatches one → both return → orchestrator summarizes

## 6. Remove legacy verification code

- [x] 6.1 Delete `backend/app/tools/report_task_result.py`
- [x] 6.2 Delete `backend/app/services/task_result_report.py`
- [x] 6.3 Remove from orchestrator.py: `_evaluate_child_task_result()`, `_evaluate_with_llM()`, `_evaluate_required_project_outputs()`, `_bind_project_expected_output()`, `_build_task_continuation_context()`, `_maybe_create_project_artifact()` gate logic, `_collect_existing_files()`, `_collect_recent_workspace_files()`
- [x] 6.4 Remove from orchestrator.py: `MAX_CHILD_TASK_ATTEMPTS` harness (lines ~912-1045)
- [x] 6.5 Remove `plan_tasks` tool injection (or keep only in coordinated mode with soft semantics)
- [x] 6.6 Update `orchestrator_prompts.py`: remove line 142 MUST rule, remove acceptanceCriteria verification injection
- [x] 6.7 Update `backend/app/schemas/dispatch.py`: simplify `DispatchPlanItem` (remove expected_outputs, required_commands, required_evidence)

## 7. Tests + Cleanup

- [x] 7.1 Delete `tests/test_task_result_evaluate.py`
- [x] 7.2 Update `tests/test_tools.py`: remove `test_report_task_result`
- [x] 7.3 New test: `tests/test_agent_loop.py` — solo loop end-to-end with mocked model
- [x] 7.4 New test: `tests/test_task_dispatch.py` — dispatch sub-agent, verify return
- [x] 7.5 New test: `tests/test_dispatch_mode_routing.py` — solo vs orchestrated routing
- [x] Run full test suite: `pytest`
- [x] 7.7 Run lint: `ruff check .`

## 8. Documentation

- [x] 8.1 Update `CLAUDE.md` §3.6: Orchestrator description reflects new paradigm
- [x] 8.2 Update or create `specs/19-unified-agent-loop.md`
- [x] 8.3 Update `openspec/specs/orchestrator/spec.md` in main specs/ folder (final state)
- [x] 8.4 Document migration notes: Conversation table schema change, tool removal
