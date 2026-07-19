## 0. Status legend

- Sections **1–12**: completed under the **v0** architecture (remote official frontend + local execution plane + official business API). Keep history; do not uncheck.
- Sections **13+**: **v1 product pivot** (local static frontend + full local backend + direct infra). These are the open implementation work.

Design authority: **D21–D30** in `design.md`. User decisions: `proposal.md` § User decisions log.

---

## 1. Scaffolding and config contracts

- [x] 1.1 Create `apps/desktop/` Tauri 2 Windows project skeleton (app id, window, single-instance plugin placeholder)
- [x] 1.2 Add package-level `official.json` / build flavor config for `webUrl`, `apiUrl`, `allowedOrigins`
- [x] 1.3 Define TypeScript `window.achatDesktop` bridge types in frontend shared/desktop module
- [x] 1.4 Document superseded status for `openspec/changes/desktop-electron-python` and point CLAUDE/OVERVIEW desktop notes to this change (docs only)

## 2. Local engine desktop runtime

- [x] 2.1 Add desktop entrypoint flags: `--bind 127.0.0.1`, `--port`, `--data-dir`, `--engine-token`, `--official-api-url`, `--allowed-origins`
- [x] 2.2 Implement `/healthz` readiness endpoint for shell probing
- [x] 2.3 Implement engine-token middleware + Origin allowlist checks on local engine routes
- [x] 2.4 Ensure desktop mode refuses non-loopback bind by default
- [x] 2.5 Add desktop data-dir layout: logs, sqlite, runtime pid/port handshake file
- [x] 2.6 Spike and select engine packaging approach (embeddable CPython vs PyInstaller one-folder) with a reproducible Windows build script

## 3. Online cloud client path (no direct DB) — v0 path

- [x] 3.1 Inventory which current backend services write PG/infra directly and mark desktop online path that must go through official HTTP API
- [x] 3.2 Implement authenticated cloud API client in local engine (reuse user access token from desktop session handoff)
- [x] 3.3 Wire online persistence for conversations/messages/agent settings through cloud client
- [x] 3.4 Implement secure retrieval of user provider keys from official settings API for direct vendor model calls
- [x] 3.5 Verify RAG/memory calls from desktop online mode go to official cloud APIs, not direct Milvus/ES/Neo4j clients

## 4. Offline SQLite and sync (v1)

- [x] 4.1 Define minimal SQLite schema for offline cache + outbox (messages/runs metadata needed for continuation)
- [x] 4.2 Implement offline write path when official API unreachable
- [x] 4.3 Implement best-effort outbox upload on reconnect
- [x] 4.4 Add conflict/failure reporting (UI status or structured error) without silent cloud overwrite
- [x] 4.5 Add unit/integration tests for offline write + reconnect upload happy path

## 5. Agent execution on local engine

- [x] 5.1 Confirm AgentRunner/adapters run in desktop mode against local tools and workspace paths
- [x] 5.2 Keep Custom adapter direct vendor calls; validate key resolution order still holds with cloud-fetched keys
- [x] 5.3 Implement CLI presence detection for `claude` / `codex` with actionable missing-CLI errors
- [x] 5.4 Preserve Windows command blacklist and path sandbox behavior under desktop data/workspace roots
- [x] 5.5 Define and implement desktop routing table: which endpoints are local-engine vs official-cloud for frontend clients

## 6. Frontend desktop bridge

- [x] 6.1 Detect `window.achatDesktop` and expose a small desktop capability helper in frontend
- [x] 6.2 Add local-engine HTTP helper that injects `X-Engine-Token` (or agreed header) and targets `engineBaseUrl`
- [x] 6.3 Route Agent execution/stream calls to local engine in desktop mode; keep auth/settings/history authority on official API as designed
- [x] 6.4 Use `selectDirectory()` for local workspace bind flows in desktop mode
- [x] 6.5 Show engine status (starting/ready/error) and restart action wired to bridge APIs
- [x] 6.6 Verify pure web path remains unchanged when bridge is absent (`pnpm typecheck` + smoke)

## 7. Tauri shell lifecycle

- [x] 7.1 Spawn local engine sidecar/process with generated engine token and data dir
- [x] 7.2 Wait for health readiness with timeout and show native/simple error page on failure
- [x] 7.3 Inject `window.achatDesktop` before/with official frontend load
- [x] 7.4 Navigate to `OFFICIAL_WEB_URL` after injection
- [x] 7.5 Implement single-instance lock + focus existing window
- [x] 7.6 Implement graceful shutdown ordering (engine then shell)
- [x] 7.7 Implement native directory picker command exposed to the bridge
- [x] 7.8 Validate https official page → http://127.0.0.1 engine calls in WebView2; if blocked, implement shell localhost reverse-proxy fallback

## 8. Session handoff (user auth ↔ local engine) — v0 path

- [x] 8.1 Design how logged-in user JWT/session is available to local engine for cloud API calls (cookie bridge, explicit token handoff endpoint, or frontend-mediated cloud writes)
- [x] 8.2 Implement chosen handoff without logging secrets
- [x] 8.3 Ensure logout/token_version invalidation stops further cloud calls from engine cleanly
- [x] 8.4 Align desktop SSE `/api/stream` user resolution with REST (`resolve_desktop_user`); do not require official JWT_SECRET on the local engine for streaming
- [x] 8.5 Add unit tests for desktop-mode SSE identity path (cloud resolve success + failure fallback)
- [x] 8.6 Frontend StreamProvider: wait for `achatDesktop` + access token before connecting; reconnect if bridge injects late (avoid locking onto official API bus)
- [x] 8.7 `fetchMessages` prefers local engine in desktop mode; send-message path best-effort re-pulls engine messages if SSE missed
- [x] 8.8 Desktop `GET /api/conversations/{id}/messages` mirrors conversation context before ownership (parity with POST)
- [x] 8.9 Document SSE/history dual-plane decisions in design (D14–D17) and frontend/local-engine/user-auth specs

## 9. Packaging, update, distribution

- [x] 9.1 Configure Tauri bundler for Windows installer including engine runtime artifacts
- [x] 9.2 Embed official endpoint config into package
- [x] 9.3 Add whole-package updater feed config and smoke check for update discovery
- [x] 9.4 Write end-user note for unsigned v1 SmartScreen behavior
- [x] 9.5 Ensure package does not bundle Claude/Codex CLIs
- [x] 9.6 Produce one internal Windows install smoke: install → launch → login → send message → bind folder → quit cleanly

## 10. Cloud API gaps (only if inventory requires) — v0 path

- [x] 10.1 Add or extend official APIs needed for desktop outbox upload / bulk sync if existing endpoints are insufficient
- [x] 10.2 Add or harden settings key read path for desktop engine with least privilege and audit logging
- [x] 10.3 Confirm invite/registration flags behave identically for desktop clients (no server topology rewrite)

## 11. Verification and docs

- [x] 11.1 Backend tests for engine token/origin middleware and offline outbox
- [x] 11.2 Frontend typecheck/lint for bridge module
- [x] 11.3 Manual matrix: web-only, desktop online, desktop offline→reconnect, missing CLI, directory bind
- [x] 11.4 Update OVERVIEW/CLAUDE desktop sections to describe Tauri + local engine + remote official frontend
- [x] 11.5 Mark old Electron desktop implementation paths deprecated in-repo README/docs references

## 12. Brand assets (desktop shell icons; web out of acceptance scope this round)

Source: `src/app/favicon.ico` → script `apps/desktop/scripts/generate-icons.py` → `apps/desktop/src-tauri/icons/*` → rebuild shell → runtime `set_icon`. See design **D20**.

- [x] 12.1 Confirm source asset: `src/app/favicon.ico` (256×256 product mark)
- [x] 12.2 Inventory **desktop** touchpoints only: `src-tauri/icons/*`, `tauri.conf.json` `bundle.icon`, window chrome via `lib.rs` `set_icon` (exclude `public/agent-icons/*`; Web favicon/login not acceptance targets)
- [x] 12.3 Add `apps/desktop/scripts/generate-icons.py` and derive full icon set (`icon.png`, multi-size `icon.ico` 16–256, `32x32.png`, `128x128.png`, `henry.w@example.net`, `icon.icns`); overwrite scaffold placeholders
- [x] 12.4 Remove nested leftover `apps/desktop/apps/` icon tree if present
- [x] 12.5 Align `tauri.conf.json` `bundle.icon` with generated files (include `icons/icon.png` + `icons/icon.ico` + png sizes + icns)
- [x] 12.6 Enable `tauri` Cargo feature `image-png`; embed `icons/icon.png` in `lib.rs` and call `window.set_icon` on `main` at setup
- [x] 12.7 Rebuild desktop shell (`cargo build` / `pnpm dev`) after icon change so the new mark is embedded in the exe (icons-on-disk alone are not enough)
- [x] 12.8 Desktop visual smoke on a **freshly built** binary: window title-bar icon shows product mark; if using an installed build, reinstall after `pnpm build` (Windows shortcut cache may lag)
- [x] 12.9 Document in `apps/desktop/README.md`: regenerate script + **must rebuild** after icon changes

---

## 13. Product pivot docs alignment (2026-07-19)

- [x] 13.1 Rewrite `proposal.md` for local static frontend + full local backend + direct infra (default official, user-overridable)
- [x] 13.2 Update `design.md` with D21–D30; mark D1/D3/D4/D12/D13 remote-frontend assumptions superseded
- [x] 13.3 Update delta specs: shell, local-engine, bridge, distribution, frontend, persistence, user-auth, platform-security, desktop-electron
- [x] 13.4 Append open implementation tasks (sections 14+) without unchecking v0 history (this file)

## 14. Config model: packaged default infra + user override

- [x] 14.1 Define desktop config schema (default infra endpoints, optional secrets refs, allowedOrigins, feature flags) replacing remote-only `webUrl`/`apiUrl` as runtime primary
- [x] 14.2 Embed default official infra config into package resources (support build-time injection from secrets; do not commit production secrets to git if policy forbids)
- [x] 14.3 Load config order: user override in `%APPDATA%/AChat` → packaged default → safe fail
- [x] 14.4 Add settings API + UI entry to view/edit custom infra server settings and revert to defaults
- [x] 14.5 Redact credentials in logs; add unit tests for config merge/override

## 15. Local engine: full backend + direct infra (retire mandatory business-API hop)

- [x] 15.1 Make desktop CLI accept infra/config path; stop requiring remote `--official-api-url` as the only online path
- [x] 15.2 Wire desktop mode `DATABASE_URL` and optional Milvus/ES/Neo4j from desktop config via existing `infra/factory` degrade rules
- [x] 15.3 Ensure auth/conversations/messages/settings/agents routes work as local engine primary APIs against remote PG
- [x] 15.4 Resolve provider keys from local primary-store settings (no mandatory CloudApiClient key fetch)
- [x] 15.5 Retire or feature-flag v0 CloudApiClient-as-only-online-path; update inventory doc
- [x] 15.6 Origin allowlist includes local UI origin (`http://127.0.0.1:<port>` and equivalents)
- [x] 15.7 Adapt offline outbox sync target from “official business API upload” to “primary DB / engine write path”

## 16. Serve packaged static frontend from engine

- [x] 16.1 Spike Next desktop static asset pipeline (export or equivalent) that works offline for core routes
- [x] 16.2 Add documented build script producing assets into desktop resources (e.g. `resources/ui`)
- [x] 16.3 Mount static files + SPA fallback on local engine
- [x] 16.4 Ensure same-origin API + UI works with engine token middleware (exempt static GETs as needed)

## 17. Shell: navigate to local UI (not remote official web)

- [x] 17.1 Change `inject_and_navigate` to open local engine UI origin after health ready
- [x] 17.2 Remove production dependency on remote `OFFICIAL_WEB_URL` navigation
- [x] 17.3 Keep bridge injection timing correct for local page load (init script and/or re-inject)
- [x] 17.4 Update shell error page copy for local-UI failures

## 18. Frontend: all desktop business traffic → local engine

- [x] 18.1 Desktop mode: `API_BASE_URL` / `authFetch` / SSE use `engineBaseUrl` (or same origin) for auth + CRUD + stream
- [x] 18.2 Remove or gate `DESKTOP_OFFICIAL_CLOUD_PATH_PREFIXES` so desktop does not call remote business API by default
- [x] 18.3 Simplify session handoff assumptions (local JWT); keep pure web path unchanged without bridge
- [x] 18.4 `pnpm typecheck` + smoke: web unchanged; desktop routes local only

## 19. Auth & session on local engine

- [x] 19.1 Login/register/refresh against local engine + primary PG
- [x] 19.2 SSE/REST share local session identity (update/remove cloud-only `resolve_desktop_user` requirement as appropriate)
- [x] 19.3 Logout / token_version still invalidates local sessions cleanly
- [x] 19.4 Tests for desktop auth + stream identity without remote business API

## 20. Packaging & release (pivot)

- [x] 20.1 Bundle static UI assets + engine + default infra config in NSIS package
- [x] 20.2 Document release steps: engine build, UI build, config injection, `pnpm build`, artifact path
- [ ] 20.3 Internal smoke: install → launch → local login → send message → bind folder → custom infra override (optional) → quit
- [x] 20.4 Update OVERVIEW/CLAUDE/`apps/desktop/README.md` to local full-stack + direct infra wording (replace remote-frontend description)

## 21. Verification matrix (pivot)

- [x] 21.1 Web-only: unchanged without bridge
- [ ] 21.2 Desktop online (default official infra): login, chat, stream, settings keys, CLI detect
- [ ] 21.3 Desktop offline/weak net: SQLite continue + reconnect sync + visible conflict
- [ ] 21.4 Desktop custom infra override: switch endpoint, restart/reconnect, smoke CRUD
- [x] 21.5 Backend tests: config load, static mount, direct DB path, middleware origin for local UI

## 22. Loopback host equivalence (D31) — localhost vs 127.0.0.1

Incident: dev UI on `http://localhost:3000`, engine on `http://127.0.0.1:<port>`. Half-aligned URL helpers caused business REST to omit `X-Engine-Token` (401) while SSE with query tokens still returned 200 — false “SSE disconnected” symptom. Design **D31**.

- [x] 22.1 Add shared loopback helpers (`alignLoopbackHost` / `sameLoopbackService` / `urlTargetsEngine`) under desktop shared module
- [x] 22.2 Wire `getApiBaseUrl`, `executionBaseUrl`, `isExecutionUrl`, `engineUrl`, StreamProvider base through the same helpers (no half-alignment)
- [x] 22.3 Desktop `authFetch` / auth-store always attach engine token for local engine targets in a loopback-aware way
- [x] 22.4 Unit tests: page=`localhost` + bridge=`127.0.0.1` still matches engine URL and attaches token
- [ ] 22.5 Manual smoke: `apps/desktop` `pnpm dev` → login → conversations/agents 200 → SSE connected badge → send message
