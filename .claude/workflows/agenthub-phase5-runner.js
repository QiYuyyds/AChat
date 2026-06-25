export const meta = {
  name: 'agenthub-phase5-runner',
  description: 'Port AgentHub AgentRunner (simple + orchestrator) from TS to Python FastAPI backend',
  phases: [
    { title: 'Understand', detail: 'map agent-runner.ts + unported deps (parallel readers)' },
    { title: 'Support', detail: 'port model_registry / dispatch_plan / project_artifact / conversation_context / evaluate' },
    { title: 'Core', detail: 'agent_runner.py (simple) then orchestrator.py (+prompts)' },
    { title: 'Verify', detail: 'full pytest + ruff + adversarial review' },
  ],
}

const ROOT = 'C:/Users/mmyy/Desktop/agents/bitdance-agenthub-main'
const PY = '.venv/Scripts/python.exe'

const SHARED = `
Porting AgentHub's backend TS (Next.js) -> Python (FastAPI). Project root: ${ROOT}. Backend: \`backend/\`.

HOUSE STYLE (match exactly; read backend/app/services/conversation_service.py and
backend/app/adapters/custom_adapter.py first): \`from __future__ import annotations\`, module docstring
"Port of src/server/...", 1-line "why" comments only, snake_case, dataclasses, Python 3.11.
Run everything with the venv: \`cd ${ROOT}/backend && ${PY} ...\`. Tests use pytest asyncio_mode=auto
(no @pytest.mark.asyncio). ruff (line-length 100; rules E,F,I,UP,B,SIM) MUST pass on new files:
\`${PY} -m ruff check <files>\`. NEVER hit a real network/LLM in tests — use the MockAdapter or monkeypatch.

DATA CONTRACT: StreamEvents + DB JSON stay camelCase on the wire. Pydantic event classes in
backend/app/schemas/events.py have snake_case fields with camelCase aliases — construct with snake_case
kwargs (populate_by_name on). Message \`parts\` and artifact \`content\` are stored as camelCase dicts
(use the ORM helpers: message.parts_list, artifact.content_dict, run.usage_dict).

ALREADY PORTED — REUSE, do not reimplement (read the files you need):
- DB: app/db/models.py (Agent, Conversation, Message, Artifact, Workspace, Attachment, AgentRun,
  ContextSummary, AppSettings), app/db/engine.get_db (async context mgr; commits on exit).
- schemas: app/schemas/{events,messages,artifacts,dispatch,requests}.py (StreamEvent union + all event
  classes, MessagePart/PartDelta, ArtifactRecord/ArtifactContent, DispatchPlanItem/TaskResultReport/
  Pending* , MessageUsage/RunUsage/DeployStatusRecord).
- services: event_bus (event_bus.publish(StreamEvent)), conversation_service, pending_dispatch_plans
  (pending_dispatch_plans + PlanReviewOutcome + PlanValidator), pending_writes/pending_questions/
  pending_bash_commands, deploy_command_service, runner_registry (set_agent_runner/get_agent_runner,
  AgentRunner Protocol, RunHandle), settings_service (get_effective_api_key(provider), get_app_settings),
  artifact_service (build_artifact_content), fs_service, bash_command_approval, task_result_report
  (parse_and_normalize, normalize_task_result_report, REPORT_TASK_RESULT_TOOL_NAME), attachment_service
  (get_attachment_absolute_path).
- adapters: base (AdapterInput, AdapterAttachment, CustomConfig, AgentPlatformAdapter), registry
  (agent_registry.get_adapter(agent_row)), session_store, mock/custom/claude adapters.
- tools: registry (tool_registry: .resolve(names)->list[ToolDef], .execute(name,args,ctx)->ToolResult),
  base (ToolContext(conversation_id, workspace_path, agent_id, run_id, cancel_event), ToolResult(ok,value,error)).
- utils: ids (new_run_id, new_artifact_id, new_message_id, ...), clock (now_ms), dispatch_run_evidence
  (RunToolEvidence, RunFileEvidence, RunCommandEvidence, record_run_*, get_run_tool_evidence,
  clear_run_tool_evidence), dispatch_file_writes (record_file_write, get_file_writes, clear_file_writes,
  detect_wave_conflicts, RunFileWrites, FileWriteConflict), workspace_utils (get_effective_cwd), platform,
  security, artifact_preview, approval.

THE RUNNER INTERFACE (already declared in app/services/runner_registry.py — your AgentRunner MUST satisfy it):
  class AgentRunner(Protocol):
    def run(self, *, agent_id, conversation_id, trigger_message_id, parent_run_id=None) -> RunHandle  # SYNC: spawn asyncio task, return RunHandle(run_id) immediately
    def abort(self, run_id) -> bool
conversation_service already calls get_agent_runner().run(...) / .abort(...). Phase 5 must call
runner_registry.set_agent_runner(AgentRunner()) at module import so the real runner is wired.

KEY PORT MAPPINGS: AbortSignal -> asyncio.Event (per run); Promise/AsyncIterable -> async/async generators;
EventEmitter -> event_bus; \`db.query.x.findFirst/findMany\` -> sqlalchemy select(...) via get_db;
\`Date.now()\` -> now_ms(). The TS source for this phase is src/server/agent-runner.ts (2746 lines) plus
the deps named per task.
`

phase('Understand')
const [uSimple, uOrch, uPrompts, uDispatchPlan, uDeps] = await parallel([
  () => agent(
    `${SHARED}\nUNDERSTAND TASK: Read src/server/agent-runner.ts and map the SIMPLE-run path + shared machinery for a Python port. Cover: the AgentRunner facade (lines ~236-270), executeRun dispatcher (~272), executeSimpleRun (~348), consumeStream (~1522), persistEvent (~1642), insertRun (~1745), finalize/finalizeOk/finalizeFailed (~1757-1955), buildAdapterInput (~1960) + pickSettingsKey (~2054) + buildWorkspaceContextBlock (~2097) + buildAgentHubToolGuidance (~2119), Semaphore (~156), and the RunArgs/RunResult/RunExecutionResult types. For each function give: signature, what it does, side effects (DB writes, events published, evidence recorded), and which already-ported Python module/symbol it should use. Note exactly which StreamEvents persistEvent persists into the messages table and how (parts array building). Be precise and structured; this guides the Core-A porter.`,
    { label: 'understand:simple', phase: 'Understand', agentType: 'Explore' },
  ),
  () => agent(
    `${SHARED}\nUNDERSTAND TASK: Read src/server/agent-runner.ts and map the ORCHESTRATOR path. Cover: executeOrchestratorRun (~394), runPlanStage (~597), waitForDispatchPlanReview (~687), executeDag + DagContext (~732-823), runChildTask (~840), maybeCreateProjectArtifact (~971), runChildTaskAttempt (~1027), evaluateChildTaskResult (~1062), the merge helpers (~1089-1124), runRequiredCommands/runSupervisorCommand (~1126-1268), command expansion + continuation prompt builders (~1270-1462), input/output binding (~1358-1444), markRemainingTasksAborted/abortedBeforeStart/publishDispatchEnd (~1462-1512). For each: signature, behavior, concurrency (Semaphore=4), retry (MAX_ATTEMPTS), how child runs are spawned (note it calls executeRun recursively — the Python seam will be an async execute_run(run_id, cancel_event, args) imported from agent_runner), DAG scheduling, conflict detection (detect_wave_conflicts), and which ported Python symbols to use (pending_dispatch_plans, dispatch_run_evidence, dispatch_file_writes, event_bus, tool bash via execute_bash_command). Structured output for the Core-B porter.`,
    { label: 'understand:orchestrator', phase: 'Understand', agentType: 'Explore' },
  ),
  () => agent(
    `${SHARED}\nUNDERSTAND TASK: Read src/server/agent-runner.ts prompt-builder section (lines ~2305-2745): buildOrchestratorPlanPrompt, buildOrchestratorAggregatePrompt, buildSubAgentPrompt, buildAggregatePrompt, renderTaskInputsXml, renderExpectedOutputsXml, renderAcceptanceCriteriaXml, renderTaskEvidenceContractXml, renderArtifactSummaryXml, renderTaskResultReportXml, xmlAttr/escapeXml, extractTextFromParts, formatSize, ensureIncludes. For each give signature + exact string/XML structure it produces (the wording matters — it is the LLM contract). Note any DB reads. Structured output for the prompts porter (Core-B).`,
    { label: 'understand:prompts', phase: 'Understand', agentType: 'Explore' },
  ),
  () => agent(
    `${SHARED}\nUNDERSTAND TASK: Read src/server/dispatch-plan.ts (771 lines) fully. Map every exported symbol the runner imports: compileAndValidateDispatchPlan, isCodeImplementationTask, CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE, and all plan validation / dependency-compilation / expectedOutputs+inputs helpers. Also read src/server/task-result-report.ts evaluateTaskResultReport + its helpers (already partially ported in backend/app/services/task_result_report.py — only parse/normalize exist; evaluate is NOT ported). Give signatures + behavior + data shapes (use schemas/dispatch.py DispatchPlanItem). This guides the dispatch_plan + evaluate porter (S3).`,
    { label: 'understand:dispatch-plan', phase: 'Understand', agentType: 'Explore' },
  ),
  () => agent(
    `${SHARED}\nUNDERSTAND TASK: Read and map these unported deps the runner needs: (1) src/shared/model-registry.ts — estimateTokens, getModelLimits (+ any tables); (2) src/server/conversation-context.ts — buildHistoryFor (spec 13: serialize MessagePart history -> OpenAI ChatCompletionMessageParam[], pinned injection, agent perspective); (3) src/server/context-compaction-service.ts — what the runner imports from it (lines 25-29 of agent-runner.ts) and whether a faithful port or a minimal correct stub is appropriate now; (4) src/server/project-artifact.ts — buildProjectFiles (used by maybeCreateProjectArtifact). For each give signatures, behavior, deps, and data shapes. This guides S1 (model_registry), S2 (conversation_context + context_compaction), S4 (project_artifact).`,
    { label: 'understand:deps', phase: 'Understand', agentType: 'Explore' },
  ),
])

phase('Support')
// Leaves first (no support-module deps among them), then modules that build on them.
const [modelReg, dispatchPlan, projectArtifact] = await parallel([
  () => agent(
    `${SHARED}\nUNDERSTAND NOTES (deps):\n${(uDeps || '').slice(0, 7000)}\n\nPORT TASK: Create backend/app/utils/model_registry.py — port of src/shared/model-registry.ts (estimate_tokens, get_model_limits + tables). Write backend/tests/test_model_registry.py covering both. Verify: pytest the test + ruff-check both files. Report files + pytest summary.`,
    { label: 'support:model_registry', phase: 'Support', agentType: 'general-purpose' },
  ),
  () => agent(
    `${SHARED}\nUNDERSTAND NOTES (dispatch-plan):\n${(uDispatchPlan || '').slice(0, 9000)}\n\nPORT TASK: Create backend/app/services/dispatch_plan.py — port of src/server/dispatch-plan.ts (compile_and_validate_dispatch_plan, is_code_implementation_task, CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE, and all plan validation / dependency / expected-output+input helpers). Use schemas/dispatch.py DispatchPlanItem (work with these Pydantic models; keep wire camelCase via aliases). Write backend/tests/test_dispatch_plan.py covering the validation + code-task detection + dependency compilation. Verify: pytest + ruff. Report files + pytest summary + the exact public API (symbol names/signatures) so S3-evaluate and Core-B can rely on it.`,
    { label: 'support:dispatch_plan', phase: 'Support', agentType: 'general-purpose' },
  ),
  () => agent(
    `${SHARED}\nUNDERSTAND NOTES (deps):\n${(uDeps || '').slice(0, 7000)}\n\nPORT TASK: Create backend/app/services/project_artifact.py — port of src/server/project-artifact.ts (build_project_files: scan workspace into a project file list for project artifacts). Reuse fs/workspace_utils where applicable. Write backend/tests/test_project_artifact.py against a temp workspace dir. Verify: pytest + ruff. Report files + pytest summary + public API.`,
    { label: 'support:project_artifact', phase: 'Support', agentType: 'general-purpose' },
  ),
])

const [convContext, evaluateExt] = await parallel([
  () => agent(
    `${SHARED}\nUNDERSTAND NOTES (deps):\n${(uDeps || '').slice(0, 8000)}\n\nmodel_registry is now ported at app/utils/model_registry.py (estimate_tokens/get_model_limits).\n\nPORT TASK: Create backend/app/services/conversation_context.py — port of src/server/conversation-context.ts (build_history_for: serialize a conversation's MessagePart history into OpenAI-format chat-message dicts for AdapterInput.history, with pinned-message injection and agent perspective; spec 13). Use get_db + ORM Message rows (message.parts_list). If context-compaction-service is needed, ALSO create backend/app/services/context_compaction_service.py with the symbols the runner imports (faithful port if tractable, otherwise a correct minimal version that preserves behavior — clearly document any reduction and add it to the deferred list in your report). Write backend/tests/test_conversation_context.py (build history from seeded messages; assert role/content mapping + pinned injection). Verify: pytest + ruff. Report files + pytest summary + public API + any deferral.`,
    { label: 'support:conversation_context', phase: 'Support', agentType: 'general-purpose' },
  ),
  () => agent(
    `${SHARED}\nUNDERSTAND NOTES (dispatch-plan + evaluate):\n${(uDispatchPlan || '').slice(0, 9000)}\n\ndispatch_plan is being ported in parallel at app/services/dispatch_plan.py (import is_code_implementation_task + CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE from there).\n\nPORT TASK: EXTEND backend/app/services/task_result_report.py — add evaluate_task_result_report(task, report, evidence) (port of evaluateTaskResultReport from src/server/task-result-report.ts) and its helpers (hasPathEvidence, hasSuccessfulCommandEvidence, verification-command detection, requiredEvidence satisfaction, etc.). \`evidence\` is app/utils/dispatch_run_evidence.RunToolEvidence; \`task\` is schemas/dispatch.DispatchPlanItem; \`report\` is the normalized report dict (or None). Return a small result object {ok: bool, error: str|None}. Add tests to backend/tests/ (new file test_task_result_evaluate.py) covering: missing report, non-complete status, failed-command evidence, missing acceptance/target/command evidence, code-task verification gate, success. Verify: pytest + ruff. Report the public API + pytest summary. Do NOT break existing task_result_report tests.`,
    { label: 'support:evaluate', phase: 'Support', agentType: 'general-purpose' },
  ),
])

phase('Core')
const coreA = await agent(
  `${SHARED}

UNDERSTAND NOTES (simple path):
${(uSimple || '').slice(0, 12000)}

All support modules are ported: app/utils/model_registry.py, app/services/dispatch_plan.py,
app/services/project_artifact.py, app/services/conversation_context.py
(+ maybe context_compaction_service.py), and task_result_report.evaluate_task_result_report.

PORT TASK (Core-A — agent_runner.py, the SIMPLE path + shared machinery). Create
backend/app/services/agent_runner.py porting from src/server/agent-runner.ts:
- @dataclass RunArgs(agent_id, conversation_id, trigger_message_id, parent_run_id=None) and RunResult/RunExecutionResult.
- class _Semaphore (asyncio, fair queue) — port of the TS Semaphore (used by orchestrator; expose it).
- class AgentRunnerImpl with:
    * run(self, *, agent_id, conversation_id, trigger_message_id, parent_run_id=None) -> RunHandle:
      SYNC — create a per-run asyncio.Event (cancel) + asyncio.create_task(self._execute_run(run_id, cancel_event, args)),
      track {run_id: (task, cancel_event)} in a dict, return RunHandle(run_id) immediately.
    * abort(self, run_id) -> bool: set the cancel event (and best-effort cancel the task); return whether found.
- async execute_run(run_id, cancel_event, args) -> RunResult: insert_run, load agent row; if agent.is_orchestrator
  call execute_orchestrator_run (LAZY import from app.services.orchestrator to avoid a circular import), else
  execute_simple_run; always finalize. Expose execute_run at module level too (orchestrator spawns children via it).
- async execute_simple_run(...): build_adapter_input -> agent_registry.get_adapter(agent).stream(input, cancel_event)
  -> consume_stream (persist + publish each event); return the RunExecutionResult.
- async consume_stream(run_id, args, agent_id, stream, cancel_event): for each StreamEvent -> persist_event +
  event_bus.publish; handle artifact_ref injection on artifact.create, tool evidence, run.usage capture, etc.
  (faithful to consumeStream).
- async persist_event(...): persist message/part/tool events into the messages table (parts_list dicts) exactly as TS.
- async insert_run / finalize / finalize_ok / finalize_failed.
- async build_adapter_input(...) + pick_settings_key + build_workspace_context_block + build_agent_hub_tool_guidance:
  assemble AdapterInput (history via conversation_context.build_history_for; api key via settings_service +
  agent.api_key override; attachments via attachment_service; tool_names resolution; system prompt with
  <workspace_info>). custom_config for the custom adapter.
- At module load: runner_registry.set_agent_runner(AgentRunnerImpl()).

IMPORTANT SEAM for Core-B: export async def execute_run(...) and the _Semaphore class and RunArgs and the
shared primitives (consume_stream, persist_event, finalize, build_adapter_input, publish helper) that the
orchestrator will import. Document this seam precisely in your report.

Also: backend/app/main.py should import app.services.agent_runner at startup so the runner registers — add that
import (read main.py first; if there is a startup/lifespan, import there or at module top).

TEST: backend/tests/test_agent_runner.py — drive a SIMPLE run end-to-end with a MOCK agent (adapter_name='mock'):
create conversation+workspace+mock agent, call runner.run(...), await the spawned task to completion, then assert
(a) an agent_runs row finished 'complete', (b) an agent message row was persisted with text parts, (c) events were
published (subscribe to event_bus). Reuse the conversation fixture pattern from backend/tests/test_tools.py.
Verify: \`cd ${ROOT}/backend && ${PY} -m pytest tests/test_agent_runner.py -q\` and ruff. Iterate until green.
Report the seam API + files + pytest summary + any faithful-port deviations.`,
  { label: 'core:agent_runner', phase: 'Core', agentType: 'general-purpose' },
)

const coreB = await agent(
  `${SHARED}

UNDERSTAND NOTES (orchestrator path):
${(uOrch || '').slice(0, 11000)}

UNDERSTAND NOTES (prompt builders):
${(uPrompts || '').slice(0, 8000)}

Core-A is done. agent_runner.py seam (import these from app.services.agent_runner):
<<SEAM>>
${(coreA || '').slice(0, 4000)}
<<END SEAM>>

PORT TASK (Core-B — orchestrator). Create:
1) backend/app/services/orchestrator_prompts.py — port the prompt/XML builders (build_orchestrator_plan_prompt,
   build_orchestrator_aggregate_prompt, build_sub_agent_prompt, build_aggregate_prompt, render_*_xml, escape_xml,
   xml_attr, extract_text_from_parts, format_size, ensure_includes). Keep the wording/XML EXACT (LLM contract).
2) backend/app/services/orchestrator.py — port execute_orchestrator_run + runPlanStage, wait_for_dispatch_plan_review,
   execute_dag (+DagContext), run_child_task (+attempt), maybe_create_project_artifact, evaluate_child_task_result,
   the merge helpers, run_required_commands/run_supervisor_command, command expansion + continuation prompts,
   input/output binding, mark_remaining_tasks_aborted, publish_dispatch_end. Use:
   - app.services.dispatch_plan (compile_and_validate_dispatch_plan, is_code_implementation_task, ...)
   - app.services.task_result_report (parse_and_normalize, evaluate_task_result_report)
   - app.services.pending_dispatch_plans (register/attach_resolver + PlanReviewOutcome) for plan review
   - app.utils.dispatch_run_evidence + dispatch_file_writes (evidence + detect_wave_conflicts)
   - app.tools.bash.execute_bash_command for required/supervisor commands
   - app.services.project_artifact.build_project_files
   - the Core-A seam: \`from app.services.agent_runner import execute_run, _Semaphore, RunArgs, ...\` to spawn child
     runs (each child is a nested run with parent_run_id). Concurrency via _Semaphore(4); retries MAX_ATTEMPTS=4.
   Keep agent_runner.execute_run's LAZY import of execute_orchestrator_run working (no circular import at module load).

TEST: backend/tests/test_orchestrator.py — at minimum cover the pure/synchronous pieces well (plan compile/validate,
DAG topological ordering, conflict detection wiring, prompt/XML rendering snapshots, evaluate gating). A full
multi-agent dispatch e2e with mock agents is a BONUS if tractable with the MockAdapter; if you can drive an
orchestrator run that plans + dispatches to mock children and aggregates, do it, else cover the building blocks.
Verify: \`cd ${ROOT}/backend && ${PY} -m pytest tests/test_orchestrator.py -q\` and ruff. Iterate until green.
Report files + pytest summary + any deviations/deferrals.`,
  { label: 'core:orchestrator', phase: 'Core', agentType: 'general-purpose' },
)

phase('Verify')
const integrate = await agent(
  `${SHARED}\nINTEGRATE: Run the FULL backend suite and ruff over everything new this phase:
\`cd ${ROOT}/backend && ${PY} -m pytest -q\`
\`cd ${ROOT}/backend && ${PY} -m ruff check app/services/agent_runner.py app/services/orchestrator.py app/services/orchestrator_prompts.py app/services/dispatch_plan.py app/services/project_artifact.py app/services/conversation_context.py app/utils/model_registry.py app/services/task_result_report.py tests\`
Fix any import/wiring/registration breakage (do NOT weaken tests). Confirm runner_registry.get_agent_runner() now
returns the real AgentRunnerImpl (not the no-op) after importing app.services.agent_runner. Report the final FULL
pytest summary line, ruff result, and every file created/edited this phase.`,
  { label: 'integrate', phase: 'Verify', agentType: 'general-purpose' },
)

const REVIEW_SCHEMA = {
  type: 'object',
  required: ['area', 'faithful', 'issues'],
  properties: {
    area: { type: 'string' },
    faithful: { type: 'boolean' },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'detail'],
        properties: {
          severity: { type: 'string', enum: ['critical', 'major', 'minor'] },
          detail: { type: 'string' },
          location: { type: 'string' },
        },
      },
    },
  },
}

const reviews = await parallel([
  () => agent(
    `${SHARED}\nADVERSARIAL REVIEW (simple path). Compare backend/app/services/agent_runner.py against src/server/agent-runner.ts (simple/consume/persist/finalize/buildAdapterInput sections) and runner_registry's Protocol. Check: run() is sync + spawns a task + returns RunHandle; abort() works; execute_run dispatches orchestrator vs simple; consume_stream persists the SAME message/part/tool/artifact shapes (camelCase) and publishes to event_bus; usage capture; build_adapter_input assembles history/api-key/attachments/tools/system-prompt faithfully; set_agent_runner wired at import; main.py imports it; cancel_event honored. Be strict. Return structured verdict.`,
    { label: 'review:simple', phase: 'Verify', schema: REVIEW_SCHEMA, agentType: 'general-purpose' },
  ),
  () => agent(
    `${SHARED}\nADVERSARIAL REVIEW (orchestrator + prompts + support). Compare backend/app/services/orchestrator.py, orchestrator_prompts.py, dispatch_plan.py, project_artifact.py, conversation_context.py, model_registry.py, and task_result_report.evaluate_task_result_report against their TS sources (src/server/agent-runner.ts orchestrator section, dispatch-plan.ts, conversation-context.ts, project-artifact.ts, shared/model-registry.ts, task-result-report.ts). Check: PLAN->EXECUTE->AGGREGATE staging, plan review wait, DAG topo + Semaphore=4 concurrency, child retry MAX_ATTEMPTS=4, conflict detection, evidence gating (evaluate_task_result_report), prompt/XML wording exactness, no circular-import at load. Flag any silently dropped behavior. Return structured verdict.`,
    { label: 'review:orchestrator', phase: 'Verify', schema: REVIEW_SCHEMA, agentType: 'general-purpose' },
  ),
])

return {
  support: {
    model_registry: (modelReg || '').slice(0, 500),
    dispatch_plan: (dispatchPlan || '').slice(0, 600),
    project_artifact: (projectArtifact || '').slice(0, 500),
    conversation_context: (convContext || '').slice(0, 700),
    evaluate: (evaluateExt || '').slice(0, 500),
  },
  core_a_seam: (coreA || '').slice(0, 2500),
  core_b_summary: (coreB || '').slice(0, 1800),
  integrate_summary: (integrate || '').slice(0, 2500),
  reviews: reviews.filter(Boolean),
}
