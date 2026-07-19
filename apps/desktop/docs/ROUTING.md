# Desktop API routing table (v1)

| Concern | Target |
|---|---|
| Login / register / refresh / logout | Official cloud `apiUrl` |
| Profile, account settings, provider keys (save) | Official cloud |
| Conversation list / cloud history authority | Official cloud (online) |
| RAG / long-term memory queries | Official cloud APIs (no direct infra) |
| Start agent run / tool execution / local SSE | Local engine `engineBaseUrl` + `X-Engine-Token` |
| Bind local folder | `window.achatDesktop.selectDirectory` → local engine workspace APIs |
| Offline writes | Local SQLite outbox |
| Session handoff (JWT → engine) | `POST /api/desktop/session` on local engine |

Frontend helpers: `src/lib/desktop.ts`, `src/shared/desktop/*`.
