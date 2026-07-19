# Desktop online path inventory (task 3.1)

Services that today write PG / infra directly and **must** go through official HTTPS API when `ACHAT_RUNTIME=desktop` (online):

| Area | Modules (examples) | Desktop online path |
|---|---|---|
| Conversations / messages | `services/*conversation*`, `api/messages.py`, `api/conversations.py` | Cloud HTTP via `CloudApiClient` / existing REST |
| Agents / settings / keys | `api/agents.py`, `api/settings.py`, `services/settings_service.py` | Cloud HTTP; keys from `/api/settings` |
| Artifacts metadata | `api/artifacts.py`, `services/artifact_service.py` | Cloud HTTP for durable metadata |
| RAG / memory / KG | `services/rag_service.py`, `memory/*`, `graph/*`, `infra/factory.py` | **No** direct Milvus/ES/Neo4j from desktop package; call official cloud APIs |
| Auth | `api/auth.py` | Official cloud only (webview) |
| Workspace files | `api/fs.py`, tools fs/bash | **Local disk** (not cloud DB) |
| Agent runs / stream | `services/agent_runner.py`, `api/stream.py` | **Local engine** execution; persist results via cloud client when online |

Offline: SQLite outbox under `%APPDATA%/AChat/sqlite` (`OfflineStore`).

## Sync API (task 10.1)

Cloud endpoint used by desktop durable writes / outbox flush:

- `POST /api/sync/messages` — UPSERT message rows for owned conversations; **does not** start Agent runs.
  Body: `{ "messages": [ { id, conversationId, role, parts, status, ... } ] }`
  Response: `{ "ok": true, "upserted": N }`
  Conflicts: engine treats HTTP 409/412 as conflict (no silent overwrite).

Legacy fallback (older cloud): `POST /api/conversations/{id}/messages` — may start a cloud run; avoid when sync is available.

## Settings key path (task 10.2)

- Desktop engine pulls `GET /api/settings` over HTTPS with the user access token only after session handoff.
- Keys never logged; resolution remains `agent.api_key` → user_settings → env.
- Least privilege: engine only needs the authenticated user's own settings row (existing ownership on `/api/settings`).
