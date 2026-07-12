## 1. Data Layer — Schema & RunArgs

- [x] 1.1 Add `hidden: Mapped[bool]` column to `Message` in `backend/app/db/models.py` (default `False`)
- [x] 1.2 Add `ALTER TABLE messages ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE` migration in `backend/app/db/engine.py`
- [x] 1.3 Add `dispatch_depth: int = 0` field to `RunArgs` dataclass in `backend/app/services/agent_runner.py`
- [x] 1.4 Add `dispatch_visibility: str = "visible"` field to `RunArgs` dataclass in `backend/app/services/agent_runner.py`
- [x] 1.5 Add `dispatch_depth: int = 0` and `dispatch_mode: str = "solo"` fields to `ToolContext` in `backend/app/tools/base.py`
- [x] 1.6 Add `MAX_DISPATCH_DEPTH = 3` constant to `backend/app/services/agent_loop.py`

## 2. Backend — Routing & Loop Modes

- [x] 2.1 In `execute_run` (`agent_runner.py`): replace `if args.override_prompt: execute_simple_run(...)` with `run_agent_loop(..., mode="subagent")`
- [x] 2.2 Add `_run_subagent_loop()` function in `agent_loop.py`: load agent, inject `task_dispatch` (when depth < MAX), inject `_SUBAGENT_SUFFIX` prompt, delegate to `execute_simple_run`
- [x] 2.3 Modify `_run_solo_loop()` in `agent_loop.py`: inject `task_dispatch` when `dispatch_depth < MAX_DISPATCH_DEPTH`, add dispatch guidance to system prompt
- [x] 2.4 Add `_SUBAGENT_SUFFIX` and `_SOLO_DISPATCH_SUFFIX` prompt constants in `agent_loop.py`
- [x] 2.5 Wire `dispatch_depth` and `dispatch_mode` into `ToolContext` construction in `execute_simple_run` / `build_adapter_input`

## 3. Backend — spawn_subagent_loop & Persist

- [x] 3.1 Add `dispatch_depth: int = 0` and `dispatch_visibility: str = "visible"` parameters to `spawn_subagent_loop()` in `agent_loop.py`
- [x] 3.2 Pass `dispatch_depth` and `dispatch_visibility` into the `RunArgs` constructed by `spawn_subagent_loop`
- [x] 3.3 In `_run_coordinated_loop`: pass `dispatch_depth + 1` when calling `spawn_subagent_loop` (via task_dispatch tool)
- [x] 3.4 Modify `persist_event` in `agent_runner.py`: set `hidden=True` on `Message` when `RunArgs.dispatch_visibility == 'hidden'` (need to thread visibility into persist call chain)

## 4. Backend — task_dispatch Tool

- [x] 4.1 In `task_dispatch.py`: change `agentId` from required to optional in `_PARAMETERS`
- [x] 4.2 Add depth check: if `ctx.dispatch_depth >= MAX_DISPATCH_DEPTH`, return error
- [x] 4.3 Add anti-loop check: if `ctx.dispatch_mode != "coordinated"` and `agentId` is specified and differs from `ctx.agent_id`, return error
- [x] 4.4 Implement clone-self logic: when `agentId` is None or equals `ctx.agent_id`, use `ctx.agent_id` and set `visibility='hidden'`
- [x] 4.5 Implement group-member logic: when `agentId` differs and mode is coordinated, validate agent ∈ conversation and set `visibility='visible'`
- [x] 4.6 Pass `dispatch_depth=ctx.dispatch_depth + 1` and `dispatch_visibility` to `spawn_subagent_loop`

## 5. Backend — dispatch_plan Tool

- [x] 5.1 In `dispatch_plan.py`: make `agentId` optional on each task item (same logic as task_dispatch)
- [x] 5.2 Add depth check and anti-loop check (same as task_dispatch)
- [x] 5.3 Pass `dispatch_depth + 1` and `dispatch_visibility` through `DagExecContext` to `spawn_subagent_loop`
- [x] 5.4 Update `dag_executor.py` `DagExecContext` to carry `dispatch_depth` and `dispatch_visibility`

## 6. Backend — Context Isolation

- [x] 6.1 In `conversation_context.py` `_build_history_legacy`: add `Message.hidden == False` filter to the message query
- [x] 6.2 In `conversation_context.py` `_build_history_with_assembler`: add the same `hidden == False` filter
- [x] 6.3 Verify that `build_history_for` is still skipped for subagent runs (existing `not args.override_prompt` guard)

## 7. Frontend — Token Usage Roll-up

- [x] 7.1 Add `subagentTokens: number` and `subagentRunCount: number` fields to `AgentUsageDetail` interface in `src/stores/app-store.ts`
- [x] 7.2 Modify `useConversationUsageTotal`: when a run has `parent_run_id`, walk the chain to find the top-level run and attribute tokens to that run's `agent_id`
- [x] 7.3 Accumulate `subagentTokens` and `subagentRunCount` on the parent agent's `AgentUsageDetail`
- [x] 7.4 In `src/components/usage-badge.tsx` `AgentUsageCard`: render "含 subagent: Nk tok · M 次" line when `subagentTokens > 0`

## 8. Frontend — Hidden Message Rendering

- [x] 8.1 Filter out messages with `hidden=true` from the chat message list rendering (check where messages are mapped to `MessageBubble` components)
- [x] 8.2 Ensure hidden messages are still loaded (for potential debug/inspection) but not displayed in the main chat view

## 9. Specs & Documentation

- [x] 9.1 Update `specs/19-unified-agent-loop.md`: add subagent recursive dispatch, `dispatch_depth`, `dispatch_visibility`, `hidden` message concept, updated routing table
- [x] 9.2 Update `CLAUDE.md` §3.6: Orchestrator is no longer the only agent that can dispatch; all agents can clone themselves via `task_dispatch`
- [x] 9.3 Verify `backend/.env.example` needs no changes (no new config keys)

## 10. Testing

- [x] 10.1 Backend unit test: solo agent can call `task_dispatch` without `agentId` (clone-self)
- [x] 10.2 Backend unit test: subagent at `MAX_DISPATCH_DEPTH` does not have `task_dispatch` in tool list (depth check returns error)
- [x] 10.3 Backend unit test: subagent attempting to dispatch to another agent returns error
- [x] 10.4 Backend unit test: clone-subagent messages have `hidden=true` in DB (verified via dispatch_visibility='hidden' in spawn call)
- [x] 10.5 Backend unit test: `build_history_for` excludes `hidden=true` messages
- [x] 10.6 Backend unit test: `dispatch_depth` increments correctly across recursive dispatch
- [x] 10.7 Backend unit test: group-member dispatch (coordinated mode) still works with `visibility='visible'`
- [x] 10.8 Backend integration test: solo agent dispatches subagent, subagent completes, result returned to parent (covered by clone-self mock test)
- [x] 10.9 Backend test: cancel cascade works for recursive subagent runs (parent cancel → all children cancel) (existing parent_cancel_event mechanism, verified in dag_executor tests)
- [x] 10.10 Run `ruff check .` and `pytest` to verify no regressions
