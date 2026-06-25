export const meta = {
  name: 'agenthub-phase6-routes',
  description: 'Port AgentHub 51 Next.js API routes to FastAPI routers calling the ready Python service layer',
  phases: [
    { title: 'Understand', detail: 'map TS route groups → request/response/service-fn/gaps' },
    { title: 'Scaffold', detail: 'add shared schemas + httpx test fixture + empty new router files (single owner)' },
    { title: 'Services', detail: 'add deferred service write-methods (artifact CRUD, settings UPSERT, attachment, deployment)' },
    { title: 'Routers', detail: 'implement each router group + its tests (one file per agent)' },
    { title: 'Integrate', detail: 'wire main.py, full pytest + ruff' },
    { title: 'Review', detail: 'adversarial contract-faithfulness review' },
  ],
}

const ROOT = 'C:/Users/mmyy/Desktop/agents/bitdance-agenthub-main'
const BE = `${ROOT}/backend`

const SHARED = `
PROJECT: AgentHub backend migration, TypeScript(Next.js) → Python(FastAPI). Working dir: ${BE}
Read ${ROOT}/CLAUDE.md for the project rules. This is phase 6 (API routes); phases 1-5 (DB, services, tools, adapters, AgentRunner) are DONE and tested (168 passing).

HARD CONVENTIONS (do not deviate):
- Wire format is camelCase. Pydantic response/request models use snake_case fields with camelCase aliases + populate_by_name=True; existing schemas in app/schemas/ already do this — reuse them, construct with snake_case kwargs, serialize with .model_dump(by_alias=True) (or return the Pydantic model and let FastAPI serialize by alias — check how existing code does it).
- The SERVICE LAYER is the source of truth and already exists under app/services/. Services manage their own DB sessions (get_db). Routes are THIN: they call service functions and translate errors to HTTP. Most routes need NO db dependency.
- Match the TS route's HTTP contract EXACTLY: method, path, status codes (200/201/202/204/4xx), request body field names, response JSON shape, and error response shape. The React frontend is unchanged and depends on these byte-for-byte. When a TS route returns e.g. { conversation: {...} } vs a bare object, mirror it exactly.
- TS sources live at ${ROOT}/src/app/api/<path>/route.ts. Read the actual TS for each route before porting.
- Python 3.11 venv: run everything with ${BE}/.venv/Scripts/python.exe (NOT bare python). ruff config: rules E,F,I,UP,B,SIM, line-length 100. Tests: pytest with asyncio_mode=auto (no marks needed).
- Existing services you can call: conversation_service, artifact_service, fs_service, settings_service, attachment_service, deployment_service, deploy_command_service, pending_writes, pending_questions, pending_bash_commands, pending_dispatch_plans, task_result_report, agent_runner (runner_registry.get_agent_runner), event_bus. Inspect each for exact function names/signatures before calling.
- DO NOT edit app/main.py (the Integrate stage wires routers). DO NOT edit app/schemas/requests.py, app/schemas/__init__.py, or tests/conftest.py (the Scaffold stage owns those). Only touch YOUR assigned router file + YOUR test file.
- /api/stream (SSE) is OUT OF SCOPE for phase 6 (it is phase 7). Leave app/api/stream.py as-is.
`

const ROUTE_GROUPS = [
  {
    key: 'conversations',
    file: 'app/api/conversations.py',
    test: 'tests/test_api_conversations.py',
    routes: [
      'conversations/route.ts (GET list, POST create)',
      'conversations/[id]/route.ts (GET, PATCH, DELETE)',
      'conversations/[id]/messages/route.ts (GET list, POST send)',
      'conversations/[id]/regenerate/route.ts (POST)',
      'conversations/[id]/compact/route.ts (POST)',
      'conversations/[id]/deploy/route.ts (POST)',
    ],
    notes: 'Replace the existing 501 placeholders. Maps to conversation_service.* (create_conversation/list_conversations/get_conversation/toggle_pin/toggle_archive/rename/set_conversation_approval_mode/add_agents/delete_conversation/clear_conversation_history/list_messages/send_message/regenerate_latest_response). PATCH dispatches by which field is present (pin/archive/rename/approval mode/add agents) — mirror the TS [id] PATCH branching exactly. compact → context_compaction_service. deploy → deploy_command_service.',
  },
  {
    key: 'messages',
    file: 'app/api/messages.py',
    test: 'tests/test_api_messages.py',
    routes: [
      'messages/[id]/edit/route.ts (POST)',
      'messages/[id]/withdraw/route.ts (POST)',
      'messages/[id]/pin/route.ts (POST)',
      'messages/[id]/bookmark/route.ts (POST)',
    ],
    notes: 'Maps to conversation_service.edit_and_resend_latest_user_message / withdraw_latest_user_message / toggle_pinned_message / toggle_bookmarked_message. Note TS passes conversationId in body or derives it — check each TS route for how it locates the conversation.',
  },
  {
    key: 'agents',
    file: 'app/api/agents.py',
    test: 'tests/test_api_agents.py',
    routes: [
      'agents/route.ts (GET list, POST create)',
      'agents/[id]/route.ts (GET?, PATCH, DELETE)',
      'agents/draft/route.ts (POST — LLM-assisted agent draft)',
    ],
    notes: 'There may be no agent_service yet — if agent CRUD lives inline in the TS route, port it directly with get_db + ORM (Agent model), following conversation_service style. agents/draft calls an LLM to draft an agent config; port faithfully (it may use an adapter/anthropic client) OR if it is heavy, implement the deterministic parts and clearly log/return what is deferred. Inspect the TS first.',
  },
  {
    key: 'artifacts',
    file: 'app/api/artifacts.py',
    test: 'tests/test_api_artifacts.py',
    routes: [
      'artifacts/route.ts (GET list by conversationId)',
      'artifacts/[id]/route.ts (GET, PATCH update→new version, DELETE)',
      'artifacts/[id]/export/route.ts (GET — download)',
      'artifacts/[id]/preview/route.ts (GET — rendered HTML preview)',
      'artifacts/[id]/versions/route.ts (GET version chain)',
    ],
    notes: 'Depends on artifact_service CRUD/version/export methods added in the Services stage (artifact_service_crud). Use artifact_preview.build_web_app_html / artifact_preview_path for preview. export/preview return file/HTML responses (FastAPI Response/HTMLResponse with correct content-type + Content-Disposition) — match TS headers.',
  },
  {
    key: 'attachments',
    file: 'app/api/attachments.py',
    test: 'tests/test_api_attachments.py',
    routes: [
      'attachments/[id]/route.ts (GET serve file, DELETE)',
      'conversations/[id]/attachments/route.ts (GET list, POST upload multipart)',
    ],
    notes: 'Depends on attachment_service upload/delete added in Services stage. Upload is multipart/form-data → FastAPI UploadFile. GET serves the raw file bytes with mime type. Match TS content-type + status.',
  },
  {
    key: 'fs',
    file: 'app/api/fs.py',
    test: 'tests/test_api_fs.py',
    routes: [
      'conversations/[id]/fs/read/route.ts',
      'conversations/[id]/fs/write/route.ts',
      'conversations/[id]/fs/listdir/route.ts',
      'fs/listdir/route.ts (global, for local-mode path picking)',
    ],
    notes: 'Maps to fs_service (read_file_in_workspace/write_file_in_workspace/list_dir_in_workspace) + workspace_utils for the global listdir. Mirror sandbox error → HTTP status mapping from TS.',
  },
  {
    key: 'pending',
    file: 'app/api/pending.py',
    test: 'tests/test_api_pending.py',
    routes: [
      'conversations/[id]/pending-writes/route.ts + [pwId]/route.ts',
      'conversations/[id]/pending-questions/route.ts + [qid]/route.ts',
      'conversations/[id]/pending-bash-commands/route.ts + [commandId]/route.ts',
      'conversations/[id]/pending-dispatch-plans/route.ts + [planId]/route.ts',
    ],
    notes: 'GET = list pending for conversation; POST [id] = resolve (approve/reject/answer/revise). Maps to pending_writes / pending_questions / pending_bash_commands / pending_dispatch_plans services. Use PendingWriteAction/PendingQuestionAnswer/PendingBashAction/PendingDispatchPlanAction request schemas (already exist).',
  },
  {
    key: 'settings',
    file: 'app/api/settings.py',
    test: 'tests/test_api_settings.py',
    routes: [
      'settings/route.ts (GET, PATCH UPSERT)',
      'settings/mobile-token/route.ts (GET/POST mobile pairing token)',
    ],
    notes: 'Depends on settings_service UPSERT + mobile-token methods added in Services stage. GET returns SettingsResponse (api keys redacted as TS does — check redaction!). PATCH upserts the single app_settings row.',
  },
  {
    key: 'runs_misc',
    file: 'app/api/runs_misc.py',
    test: 'tests/test_api_runs_misc.py',
    routes: [
      'runs/[id]/abort/route.ts',
      'search/route.ts',
      'usage/summary/route.ts',
      'platform/route.ts',
      'connection-hints/route.ts',
      'deployments/[id]/download/[kind]/route.ts',
      'internal/agenthub-tools/route.ts (port if simple; else return deferred note)',
    ],
    notes: 'abort → conversation_service.abort_run (or runner_registry.get_agent_runner().abort). search → port the TS search (SQL LIKE over messages/conversations) using SearchRequest/SearchResponse schemas. usage/summary → aggregate agent_runs usage. platform → return host platform info. connection-hints → LAN/connection info for mobile. deployments download/[kind] → deployment_service zip/asset download (added in Services stage). Keep each route faithful to its TS; if one is genuinely large (e.g. internal tools), implement the rest and clearly note the deferral in your return.',
  },
  {
    key: 'mobile',
    file: 'app/api/mobile/routes.py',
    test: 'tests/test_api_mobile.py',
    routes: [
      'mobile/snapshot/route.ts',
      'mobile/conversations/[id]/route.ts',
      'mobile/conversations/[id]/messages/route.ts',
      'mobile/conversations/[id]/messages/[messageId]/edit/route.ts',
      'mobile/conversations/[id]/messages/[messageId]/withdraw/route.ts',
      'mobile/conversations/[id]/regenerate/route.ts',
      'mobile/artifacts/[id]/route.ts',
      'mobile/pending-questions/[id]/route.ts',
      'mobile/pending-writes/[id]/route.ts',
    ],
    notes: 'Mobile companion API (spec 14). Thin wrappers over the same services as the desktop routes, often with a mobile-token auth check and trimmed payloads. Port faithfully; reuse conversation_service / pending_* / artifact_service. If mobile-token auth depends on settings mobile-token (Services stage), wire it.',
  },
]

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['area', 'faithful', 'issues'],
  properties: {
    area: { type: 'string' },
    faithful: { type: 'boolean' },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['severity', 'detail', 'location'],
        properties: {
          severity: { type: 'string', enum: ['critical', 'major', 'minor'] },
          detail: { type: 'string' },
          location: { type: 'string' },
        },
      },
    },
  },
}

// ── Understand ────────────────────────────────────────────────────────────────
phase('Understand')
const surveyAreas = [
  'conversations + messages routes (conversations/**, messages/**)',
  'agents + artifacts routes (agents/**, artifacts/**)',
  'attachments + fs + pending routes (attachments/**, conversations/[id]/fs/**, conversations/[id]/pending-*/**, fs/listdir)',
  'settings + runs/search/usage/platform/connection-hints/deployments/internal + mobile/** routes',
]
const surveys = await parallel(
  surveyAreas.map((area) => () =>
    agent(
      `${SHARED}\n\nUNDERSTAND TASK. Survey these TS routes: ${area}.\n` +
        `For EACH route.ts file in that set, read it and report: HTTP method(s), full path, request body/query shape, exact response JSON shape, status codes, error shapes, and WHICH existing Python service function(s) it should map to (inspect app/services/ to confirm names exist). Flag any route whose backing service method is MISSING (these become Services-stage work): artifact CRUD/version/export, settings UPSERT/mobile-token, attachment upload/delete, deployment zip/asset download. Be concrete and cite file paths. This is read-only — do not write code.`,
      { label: `survey:${area.slice(0, 24)}`, phase: 'Understand', agentType: 'Explore' }
    )
  )
)
const surveyDigest = surveys.filter(Boolean).join('\n\n---\n\n').slice(0, 12000)

// ── Scaffold (single owner of shared files) ───────────────────────────────────
phase('Scaffold')
const scaffold = await agent(
  `${SHARED}\n\nSCAFFOLD TASK (you are the SINGLE owner of shared files this phase — no other agent touches them).\n` +
    `Survey findings:\n${surveyDigest}\n\n` +
    `Do ALL of the following, then verify:\n` +
    `1. Add any MISSING Pydantic request/response models the routers will need to app/schemas/requests.py (and export them from app/schemas/__init__.py). Follow the existing snake_case-field + camelCase-alias + populate_by_name pattern. Cover: artifact update/version/list responses, attachment upload/list responses, fs read/write/listdir request+response, settings already has UpdateSettingsRequest/SettingsResponse (extend only if needed), search already done, usage summary response, platform response, connection-hints response, mobile snapshot response, mobile-token request/response. Only add what is actually missing — reuse existing models (list them first).\n` +
    `2. Add an httpx-based async test client fixture to tests/conftest.py named \`api_client\` that mounts the FastAPI app (from app.main) with ASGITransport and shares the same test DB as the existing \`db\` fixture. Inspect app/main.py to build the app correctly (lifespan registers the runner). Ensure it works with asyncio_mode=auto. If httpx is not installed, install it into the venv and add it to pyproject.toml [dev] + requirements.txt (note this in your report — it is a test-only dep).\n` +
    `3. Create these NEW empty router files, each containing only the house docstring + \`from fastapi import APIRouter\` + \`router = APIRouter()\` (router-stage agents will fill them): app/api/attachments.py, app/api/fs.py, app/api/pending.py, app/api/runs_misc.py, app/api/mobile/routes.py (create app/api/mobile/__init__.py if missing).\n` +
    `Verify: \`${BE}/.venv/Scripts/python.exe -c "import app.schemas; import app.api.attachments, app.api.fs, app.api.pending, app.api.runs_misc, app.api.mobile.routes"\` imports clean, and \`ruff check\` is clean on every file you touched. Run the existing full test suite to confirm you broke nothing: \`${BE}/.venv/Scripts/python.exe -m pytest -q\`.\n` +
    `Report: the exact list of schema model names now available (so router agents can import them), the conftest fixture name + how to use it, and the new router file paths.`,
  { label: 'scaffold', phase: 'Scaffold' }
)

// ── Services (deferred write-methods; each owns its own service file) ──────────
phase('Services')
const serviceJobs = [
  {
    label: 'svc:artifact',
    prompt:
      'Add the deferred artifact CRUD + version-chain + export methods to app/services/artifact_service.py (TS source: src/server/artifact-service.ts or wherever artifact CRUD lives — search src/server). Needed by artifacts routes: list by conversation, get one, update→creates a new version (parent_artifact_id chain, version increment), delete, list versions, and an export/serialize helper. Reuse build_artifact_content. Keep camelCase content dicts. Add focused unit tests to tests/test_artifact_service.py.',
  },
  {
    label: 'svc:settings',
    prompt:
      'Add settings UPSERT + mobile-token methods to app/services/settings_service.py (currently read-only). TS source: src/server/settings-service.ts. Needed: upsert/patch the single app_settings row (PATCH /api/settings), and mobile pairing token get/create (GET+POST /api/settings/mobile-token). Mirror TS redaction of API keys on read. Add tests to tests/test_settings_service.py.',
  },
  {
    label: 'svc:attachment',
    prompt:
      'Add attachment upload + delete methods to app/services/attachment_service.py (currently read-only). TS source: src/server/attachment-service.ts. Needed: save an uploaded file into the conversation workspace/attachments area, create the attachments row (kind image/file, mime_type, file_path), and delete (row + file). Enforce the same path-safety/size limits TS does. Add tests to tests/test_attachment_service.py.',
  },
  {
    label: 'svc:deployment',
    prompt:
      'Add deployment asset serving + zip download methods to app/services/deployment_service.py (currently create/publish only). TS source: src/server/deployment-service.ts + the deployments/[id]/download/[kind] route. Needed: locate a deployment by id, produce a zip (or serve a single asset) for the given kind. Add tests to tests/test_deployment_service.py.',
  },
]
const serviceResults = await parallel(
  serviceJobs.map((j) => () =>
    agent(
      `${SHARED}\n\nSERVICES TASK. ${j.prompt}\n\n` +
        `You OWN your service file + your test file exclusively. Do NOT edit schemas/conftest/main/router files. Search src/server for the TS source first. Verify: ruff clean on your files + \`${BE}/.venv/Scripts/python.exe -m pytest tests/<your_test_file> -q\` green. Report the exact public function names + signatures you added so the router stage can call them.`,
      { label: j.label, phase: 'Services' }
    )
  )
)
const serviceDigest = serviceResults.filter(Boolean).join('\n\n---\n\n').slice(0, 8000)

// ── Routers (one file per agent → zero write conflicts) ───────────────────────
const routerResults = await pipeline(
  ROUTE_GROUPS,
  (g) =>
    agent(
      `${SHARED}\n\nROUTER TASK — group "${g.key}". You exclusively own ${g.file} and ${g.test}.\n\n` +
        `Scaffold report (available schemas + test fixture):\n${(scaffold || '').slice(0, 4000)}\n\n` +
        `Services added this phase (call these for any previously-missing methods):\n${serviceDigest}\n\n` +
        `Implement these routes faithfully (read each TS route.ts under ${ROOT}/src/app/api/ first):\n- ${g.routes.join('\n- ')}\n\n` +
        `Notes: ${g.notes}\n\n` +
        `Write the FastAPI route handlers in ${g.file} (define \`router = APIRouter()\`; replace any 501 placeholders). Translate service/sandbox errors to the same HTTP status codes the TS route returns. Then write tests in ${g.test} using the \`api_client\` fixture from conftest (and the existing \`db\`/\`agents\` fixtures — inspect tests/conftest.py + tests/test_tools.py for fixture usage). Cover happy path + at least one error path per route.\n\n` +
        `Verify ONLY your files: ruff clean on ${g.file} and ${g.test}, and \`${BE}/.venv/Scripts/python.exe -m pytest ${g.test} -q\` green. Do NOT edit main.py — report that your router needs wiring as \`app.api.${g.key === 'mobile' ? 'mobile.routes' : g.key}\`. Report: routes implemented, anything deferred (with reason), and your router module path + variable.`,
      { label: `router:${g.key}`, phase: 'Routers' }
    ),
  (res, g) => ({ key: g.key, file: g.file, summary: res })
)

// ── Integrate ─────────────────────────────────────────────────────────────────
phase('Integrate')
const integrate = await agent(
  `${SHARED}\n\nINTEGRATE TASK. All router files are implemented. Now:\n` +
    `1. Wire every router into app/main.py via app.include_router(..., prefix="/api"). Routers to include: app.api.conversations, app.api.messages, app.api.agents, app.api.artifacts, app.api.attachments, app.api.fs, app.api.pending, app.api.settings, app.api.runs_misc, app.api.mobile.routes (mobile may need prefix handling so paths resolve to /api/mobile/** — verify against TS paths). Keep existing stream/search/runs includes correct (avoid double-registering the same paths; runs_misc may supersede the old runs.py + search.py — if so, remove the superseded includes and delete the now-empty old files, but only after confirming runs_misc covers them).\n` +
    `2. Run the FULL suite: \`${BE}/.venv/Scripts/python.exe -m pytest -q\`. Then \`${BE}/.venv/Scripts/python.exe -m ruff check app tests\`.\n` +
    `3. Fix any integration failures (route collisions, import cycles, ruff). You MAY edit main.py and any phase-6 file to resolve integration issues, but do NOT weaken tests to pass.\n` +
    `Router stage results:\n${JSON.stringify(routerResults.filter(Boolean).map((r) => ({ key: r.key, file: r.file })))}\n\n` +
    `Report: final \`pytest\` pass count, final \`ruff\` status (app + tests), the complete list of wired routes, any files deleted, and anything still deferred.`,
  { label: 'integrate', phase: 'Integrate' }
)

// ── Review (adversarial, contract faithfulness) ───────────────────────────────
phase('Review')
const reviewAreas = [
  'conversations + messages + agents routers — verify HTTP method/path/status/request/response/error JSON match the TS routes byte-for-byte (the unchanged React frontend depends on this)',
  'artifacts + attachments + fs + pending + settings + runs_misc + mobile routers — same contract-faithfulness check vs TS, plus correct use of the newly-added service methods and file/binary responses (content-type, Content-Disposition, status codes)',
]
const reviews = await parallel(
  reviewAreas.map((area) => () =>
    agent(
      `${SHARED}\n\nADVERSARIAL REVIEW. Area: ${area}.\n` +
        `Compare each implemented Python route against its TS source (${ROOT}/src/app/api/**/route.ts). Look for contract drift the frontend would notice: wrong status code, renamed/missing JSON field, wrong nesting ({x:{}} vs {}), camelCase vs snake_case leaking on the wire, wrong error shape, missing route, wrong content-type/headers on file responses. Also check services were called correctly. Default to skeptical. Set faithful=false if ANY major/critical drift exists. Cite file:line on both sides.`,
      { label: `review:${area.slice(0, 20)}`, phase: 'Review', schema: REVIEW_SCHEMA }
    )
  )
)

return {
  scaffold: (scaffold || '').slice(0, 1500),
  services: serviceResults.filter(Boolean).map((r) => r.slice(0, 600)),
  routers: routerResults.filter(Boolean),
  integrate: (integrate || '').slice(0, 3000),
  reviews: reviews.filter(Boolean),
}
