export const meta = {
  name: 'agenthub-phase4-adapters',
  description: 'Port AgentHub Agent adapters (Mock/Custom/Claude) from TS to Python FastAPI backend',
  phases: [
    { title: 'Foundation', detail: 'base.py + ids + custom_provider_client' },
    { title: 'Port', detail: 'mock / custom / claude adapters + tests (parallel)' },
    { title: 'Integrate', detail: 'registry.py + full pytest + ruff' },
    { title: 'Review', detail: 'adversarial faithfulness review per adapter' },
  ],
}

const ROOT = 'C:/Users/mmyy/Desktop/agents/bitdance-agenthub-main'
const PY = '.venv/Scripts/python.exe'

const SHARED = `
You are porting AgentHub's backend from TypeScript (Next.js) to Python (FastAPI).
Project root: ${ROOT}. The Python backend lives under \`backend/\`.

ABSOLUTE RULES (project CLAUDE.md + phase-2/3 conventions already in the repo):
- Match the existing house style EXACTLY. Read a few already-ported files first:
  backend/app/tools/bash.py, backend/app/tools/registry.py, backend/app/services/conversation_service.py,
  backend/app/services/pending_writes.py. Mirror their docstring style (1-line "why" comments,
  module docstring saying "Port of src/server/..."), \`from __future__ import annotations\`,
  dataclasses, snake_case.
- Python 3.11. Run everything with the venv: \`cd ${ROOT}/backend && ${PY} ...\`.
- Tests use pytest with asyncio_mode=auto (no @pytest.mark.asyncio needed). ruff config:
  line-length 100, rules E,F,I,UP,B,SIM. Your new files MUST be ruff-clean:
  \`${PY} -m ruff check <files>\`.
- CRITICAL DATA CONTRACT: StreamEvents and DB JSON stay camelCase on the wire. The Pydantic
  event classes in backend/app/schemas/events.py already have snake_case fields with camelCase
  aliases — construct them with snake_case kwargs (populate_by_name is on). Tool result VALUES
  and artifact content dicts are camelCase (e.g. artifactId, absolutePath).
- NEVER hit a real network/LLM in tests. Mock the SDK client.

THE ADAPTER CONTRACT (already-decided design — read backend/app/adapters/base.py after Foundation writes it):
- AdapterInput (dataclass) fields: agent_id, conversation_id, run_id, prompt, workspace_path,
  system_prompt, api_key (str|None), api_base_url (str|None), model_id (str|None),
  tool_names (list[str]), attachments (list[AdapterAttachment]|None),
  history (list[dict]|None, OpenAI chat-message dicts), custom_config (CustomConfig|None).
- AdapterAttachment (dataclass): id, file_name, mime_type, kind ('image'|'file'), abs_path.
- CustomConfig (dataclass): model_provider (str), supports_vision (bool=False).
- AgentPlatformAdapter (ABC): \`name\` property (one of 'mock'|'custom'|'claude-code'|'codex'),
  and \`async def stream(self, input: AdapterInput, cancel_event: asyncio.Event) -> AsyncIterator[StreamEvent]\`.
  The TS \`AbortSignal\` becomes \`asyncio.Event\` (check \`cancel_event.is_set()\` to abort).

EVENTS to yield (from app.schemas.events; timestamps via \`from app.utils.clock import now_ms\`):
  MessageStartEvent(conversation_id, timestamp, message_id, agent_id, run_id)
  PartStartEvent(..., message_id, part_index, part={"type":"text"|"thinking"|"code","content":""[,"language":...]})
  PartDeltaEvent(..., message_id, part_index, delta={"type":"text.append"|"thinking.append"|"code.append","text":chunk})
  PartEndEvent(..., message_id, part_index)
  ToolCallEvent(..., message_id, call_id, tool_name, args)
  ToolResultEvent(..., message_id, call_id, result, is_error)
  MessageUsageEventPayload(..., message_id, usage=MessageUsage(input_tokens, output_tokens, cache_read_tokens))
  MessageEndEvent(..., message_id)
  RunUsageEvent(..., run_id, usage=RunUsage(input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, last_input_tokens, model))
  ArtifactCreateEvent(..., artifact=ArtifactRecord(...))   # ArtifactRecord from app.schemas.artifacts
  DeployStatusEvent(..., message_id, deployment=DeployStatusRecord)  # from app.schemas.messages
  MessageUsage / RunUsage are in app.schemas.messages.

TOOLS (already ported in phase 3 — reuse, do not reimplement):
  from app.tools.registry import tool_registry
  from app.tools.base import ToolContext, ToolResult
  ctx = ToolContext(conversation_id=..., workspace_path=..., agent_id=..., run_id=..., cancel_event=...)
  tool_defs = tool_registry.resolve(input.tool_names)   # list[ToolDef] each with .name/.description/.parameters
  result = await tool_registry.execute(name, args, ctx)  # ToolResult(ok: bool, value, error)
  After a successful write_artifact tool call, emit ArtifactCreateEvent by loading the artifact row
  (sqlalchemy select Artifact; use artifact.content_dict). After deploy_artifact/deploy_workspace
  success whose value looks like a DeployStatusRecord, emit DeployStatusEvent.
  IDs: from app.utils.ids import new_message_id, new_tool_call_id.
`

phase('Foundation')
const foundation = await agent(
  `${SHARED}

TASK (Foundation — write the shared adapter contract). Do these in backend/:

1) Create backend/app/adapters/base.py — port of src/server/adapters/types.ts. Define, in snake_case:
   - @dataclass AdapterAttachment(id, file_name, mime_type, kind, abs_path)
   - @dataclass CustomConfig(model_provider: str, supports_vision: bool = False)
   - @dataclass AdapterInput(... all fields listed in the contract above; attachments/history/custom_config default None ...)
   - class AgentPlatformAdapter(ABC) with abstract \`name\` property and abstract async \`stream(self, input, cancel_event) -> AsyncIterator[StreamEvent]\`.
   Use \`from __future__ import annotations\`, abc.ABC/abstractmethod, typing AsyncIterator, asyncio.
   Module docstring: "Port of src/server/adapters/types.ts. See specs/05-adapter-interface.md."

2) Add \`new_tool_call_id()\` -> "call_<nanoid>" to backend/app/utils/ids.py (match the existing _gen_id helper).

3) Create backend/app/adapters/custom_provider_client.py — port of src/server/adapters/custom-provider-client.ts
   AND inline the two validators from src/shared/openai-compatible.ts (validate_openai_compatible_base_url,
   validate_openai_compatible_api_key). Function resolve_custom_provider_client_config(provider, override_key, api_base_url)
   returns a dataclass/dict {api_key, base_url?} for providers deepseek / volcano-ark / openai / openai-compatible,
   reading env DEEPSEEK_API_KEY / ARK_API_KEY / OPENAI_API_KEY as fallback (use os.environ). Same defaults/URLs as the TS.

Read the TS sources first: src/server/adapters/types.ts, src/server/adapters/custom-provider-client.ts,
src/shared/openai-compatible.ts, src/server/ids.ts. Confirm imports resolve:
\`cd ${ROOT}/backend && ${PY} -c "import app.adapters.base, app.adapters.custom_provider_client, app.utils.ids"\`
and ruff-check the 3 touched files. Report the exact final AdapterInput field list and the base.py public API so downstream agents can rely on it.`,
  { label: 'foundation', phase: 'Foundation', agentType: 'general-purpose' },
)

phase('Port')
const ADAPTERS = [
  {
    key: 'mock',
    file: 'backend/app/adapters/mock_adapter.py',
    test: 'backend/tests/test_mock_adapter.py',
    src: 'src/server/adapters/mock-adapter.ts',
    extra: `Port MockAdapter: class MockAdapter(AgentPlatformAdapter), name='mock'. Scripted streaming —
pick a script by prompt keywords (greeting/code/tool/default, keep the same Chinese scripts), stream
text/thinking/code char-chunks with small asyncio.sleep, emit one tool.call+tool.result for the tool script,
honor cancel_event.is_set(). Replace setTimeout with \`await asyncio.sleep(...)\` (use small values like 0.005
so tests are fast). Use new_message_id / new_tool_call_id. NO real LLM. Test: drive stream() to completion for
a couple prompts and assert the event type sequence (message.start ... part.* ... message.end) and that
cancel_event short-circuits.`,
  },
  {
    key: 'custom',
    file: 'backend/app/adapters/custom_adapter.py',
    test: 'backend/tests/test_custom_adapter.py',
    src: 'src/server/adapters/custom-agent-adapter.ts',
    extra: `Port CustomAgentAdapter as class CustomAdapter(AgentPlatformAdapter), name='custom', using the
\`openai\` Python SDK's AsyncOpenAI. Faithfully port the tool loop (MAX_TURNS=8): build messages [system, *history, user],
stream chat.completions with stream=True and stream_options={"include_usage": True}, accumulate text /
reasoning_content (thinking) / tool_calls deltas, emit part.* events, write back the assistant message,
execute tools via tool_registry on tool_calls, push tool results back, track per-message + per-run usage,
emit message.usage / run.usage, and emit artifact.create (after write_artifact) + deploy.status (after deploy_*).
Port multimodal user content (build_multimodal_user_content, MAX_IMAGES_PER_MESSAGE=5, read image base64 from
attachment abs_path). Client built via a module-level \`_build_client(provider, key, base_url)\` that wraps
AsyncOpenAI(**resolve_custom_provider_client_config(...), max_retries=2) — keep it module-level so tests can
monkeypatch it. Convert ToolDef -> OpenAI tool via {"type":"function","function":{name,description,parameters}}.
Check cancel_event between chunks/turns.
TEST (no network): monkeypatch the module's _build_client to return a fake async client whose
chat.completions.create returns an async generator yielding fake ChatCompletionChunk-like objects (you can use
simple namespace/dataclass stubs exposing .choices[0].delta.content / .tool_calls / .finish_reason and a final
chunk with .usage). Assert: (1) a no-tool response yields message.start, part.start/delta/end(text), message.end,
run.usage; (2) a response that calls a real ported tool (e.g. report_task_result or write_artifact with a DB
fixture) emits tool.call + tool.result and loops. Reuse the conversation/workspace fixture pattern from
backend/tests/test_tools.py (copy its conversation fixture into this test file or a conftest).`,
  },
  {
    key: 'claude',
    file: 'backend/app/adapters/claude_adapter.py',
    test: 'backend/tests/test_claude_adapter.py',
    src: 'src/server/adapters/custom-agent-adapter.ts',
    extra: `IMPORTANT: This is NOT a line-by-line port of src/server/adapters/claude-code-adapter.ts (that wraps the
Claude Code CLI SDK). Per the migration plan's confirmed decision, the Python Claude adapter uses the
ANTHROPIC MESSAGES API + tool loop directly (the user routes Anthropic via a gateway). So mirror the STRUCTURE
of custom-agent-adapter.ts (the tool loop) but use the \`anthropic\` Python SDK's AsyncAnthropic Messages API.
class ClaudeAdapter(AgentPlatformAdapter), name='claude-code'.
Design: build messages as Anthropic message dicts [{"role":"user","content":...}] (system goes in the top-level
\`system\` param, not in messages). history (OpenAI-format dicts) should be converted to Anthropic role/content
(user/assistant; map tool messages to tool_result content blocks) — keep it pragmatic but correct. Tools: convert
each ToolDef to {"name","description","input_schema": parameters}. Stream via
\`async with client.messages.stream(model=..., max_tokens=..., system=..., messages=..., tools=...) as stream:\`
and iterate events: emit text part.* from text deltas; collect tool_use blocks; on stop_reason 'tool_use' run the
tools via tool_registry, append assistant tool_use + user tool_result blocks, loop (MAX_TURNS=8); else finish.
Emit message.start/end, part.*, tool.call/tool.result, message.usage, run.usage (map Anthropic usage:
input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens), and artifact.create /
deploy.status like the custom adapter. Build client via module-level \`_build_client(api_key, base_url)\` wrapping
AsyncAnthropic(api_key=..., base_url=...) so tests can monkeypatch. Check cancel_event.
TEST (no network): monkeypatch _build_client to return a fake AsyncAnthropic whose messages.stream(...) returns
an async context manager yielding fake stream events (text deltas + a final message with usage; optionally a
tool_use path). Assert the emitted StreamEvent sequence for a simple text answer, and run.usage at the end.
Reuse the conversation/workspace fixture from backend/tests/test_tools.py.`,
  },
]

const ported = await parallel(
  ADAPTERS.map((a) => () =>
    agent(
      `${SHARED}

Foundation is done. base.py public API and notes:
<<FOUNDATION>>
${foundation}
<<END FOUNDATION>>

TASK (Port the ${a.key} adapter). Read the TS source ${a.src} and the already-ported
backend/app/adapters/base.py, backend/app/schemas/events.py, backend/app/schemas/messages.py,
backend/app/schemas/artifacts.py, backend/app/tools/registry.py, backend/app/tools/base.py.

Write ${a.file} and a test ${a.test}.
${a.extra}

Then verify ONLY your files:
\`cd ${ROOT}/backend && ${PY} -m pytest ${a.test.replace('backend/', '')} -q\` (must pass)
\`cd ${ROOT}/backend && ${PY} -m ruff check ${a.file.replace('backend/', '')} ${a.test.replace('backend/', '')}\` (must be clean)
Iterate until both pass. Report what you wrote, the test result (paste the pytest summary line), and any
deviations from the TS source with justification.`,
      { label: `port:${a.key}`, phase: 'Port', agentType: 'general-purpose' },
    ),
  ),
)

phase('Integrate')
const integrate = await agent(
  `${SHARED}

All three adapters are ported: backend/app/adapters/mock_adapter.py, custom_adapter.py, claude_adapter.py.

TASK (Integrate):
1) Create backend/app/adapters/registry.py — port of src/server/adapters/registry.ts. class AgentRegistry with
   register(adapter) and get_adapter(agent) (raise on unknown adapter_name; message includes agent name/id).
   _build_registry() registers MockAdapter(), CustomAdapter(), ClaudeAdapter() ONLY (Codex is deferred — add a
   1-line comment). Module singleton \`agent_registry = _build_registry()\`. get_adapter takes an Agent ORM row
   (use agent.adapter_name). Note: claude adapter name is 'claude-code'.
2) Run the FULL backend test suite and ruff over all new files:
   \`cd ${ROOT}/backend && ${PY} -m pytest -q\`
   \`cd ${ROOT}/backend && ${PY} -m ruff check app/adapters\`
   Fix any import/wiring/registration issues (do NOT weaken tests). If the agent .name values don't match the
   registry keys ('mock'/'custom'/'claude-code'), fix the adapter .name.
Report the final FULL pytest summary line (e.g. "N passed") and ruff result. List every file you created/edited.`,
  { label: 'integrate', phase: 'Integrate', agentType: 'general-purpose' },
)

phase('Review')
const REVIEW_SCHEMA = {
  type: 'object',
  required: ['adapter', 'faithful', 'issues'],
  properties: {
    adapter: { type: 'string' },
    faithful: { type: 'boolean', description: 'true if the Python port faithfully matches the TS behavior/contract' },
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

const reviews = await parallel(
  ADAPTERS.map((a) => () =>
    agent(
      `${SHARED}

Adversarially REVIEW the ported ${a.key} adapter for faithfulness and correctness. Read the TS source
${a.src} (for claude, also note it intentionally diverges to the Anthropic Messages API — judge it against the
tool-loop structure of custom-agent-adapter.ts, NOT claude-code-adapter.ts) and the Python file ${a.file} and
its test ${a.test}.

Check specifically:
- Event sequence + field names match the contract (camelCase aliases, correct part/delta dict shapes).
- Tool loop correctness: MAX_TURNS, tool_calls accumulation, results pushed back, artifact.create/deploy.status
  emission, usage accounting (per-message and per-run).
- cancel_event honored; no real network in tests; SDK client is monkeypatchable.
- Faithfulness to the TS behavior; flag silently dropped behaviors.
- The test actually exercises the streaming loop (not just imports) and would catch regressions.

Return ONLY the structured verdict. Be strict: list concrete issues with severity and file location.`,
      { label: `review:${a.key}`, phase: 'Review', schema: REVIEW_SCHEMA, agentType: 'general-purpose' },
    ),
  ),
)

return {
  foundation_summary: foundation.slice(0, 1500),
  integrate_summary: integrate.slice(0, 2000),
  port_summaries: ported.map((p, i) => ({ adapter: ADAPTERS[i].key, summary: (p || '').slice(0, 800) })),
  reviews: reviews.filter(Boolean),
}
