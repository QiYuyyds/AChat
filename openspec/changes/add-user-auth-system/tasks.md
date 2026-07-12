## 1. Backend Auth Infrastructure

- [ ] 1.1 Add `bcrypt` and `PyJWT` dependencies to `pyproject.toml`
- [ ] 1.2 Add `jwt_secret`, `jwt_access_token_expiry`, `jwt_refresh_token_expiry`, `allow_registration` fields to `config.py` Settings class
- [ ] 1.3 Add `JWT_SECRET` and `ALLOW_REGISTRATION` to `backend/.env.example` with documentation
- [ ] 1.4 Create `backend/app/auth/__init__.py` module
- [ ] 1.5 Create `backend/app/auth/password.py` — `hash_password()` and `verify_password()` using bcrypt (cost factor 12)
- [ ] 1.6 Create `backend/app/auth/jwt_handler.py` — `create_access_token(user)`, `create_refresh_token(user)`, `verify_token(token)` returning payload with `sub`, `email`, `type`, `exp`, `iat`, `ver`
- [ ] 1.7 Create `backend/app/auth/dependencies.py` — `get_current_user(request, db)` FastAPI dependency that reads JWT from cookie (production) or Authorization header (dev), verifies it, and returns the User object
- [ ] 1.8 Create `backend/app/auth/service.py` — `register_user()`, `authenticate_user()`, `refresh_access_token()`, `change_password()`, `logout_all_devices()` business logic

## 2. Database Schema & Migration

- [ ] 2.1 Add `User` model to `backend/app/db/models.py` with fields: `id`, `email`, `name`, `password_hash`, `avatar_url`, `token_version`, `created_at`, `updated_at`
- [ ] 2.2 Add `user_id` column to `Agent` model (nullable — NULL for builtin agents)
- [ ] 2.3 Add `user_id` column to `Conversation` model (NOT NULL)
- [ ] 2.4 Add `user_id` column to `Document` model (NOT NULL)
- [ ] 2.5 Add `user_id` column to `McpServer` model (NOT NULL)
- [ ] 2.6 Add `user_id` column to `LongTermMemory` model (extend existing scope concept)
- [ ] 2.7 Add `user_id` column to `MemoryNode` and `ChatHistory` models
- [ ] 2.8 Create `GlobalSettings` model (single-row, deployment config fields moved from AppSettings)
- [ ] 2.9 Rename `AppSettings` to `UserSettings`, change PK from `id='singleton'` to `user_id` FK→users
- [ ] 2.10 Add migration statements to `engine.py` `_migrate_columns()` — `ADD COLUMN IF NOT EXISTS user_id` for all affected tables, `CREATE TABLE IF NOT EXISTS users`, `CREATE TABLE IF NOT EXISTS global_settings`
- [ ] 2.11 Create `backend/scripts/migrate_to_multi_user.py` — creates default user (email/password from env), back-fills `user_id` on all existing rows, copies deployment config to `global_settings`, renames `app_settings` → `user_settings`

## 3. Auth API Router

- [ ] 3.1 Create `backend/app/api/auth.py` with `APIRouter`
- [ ] 3.2 Implement `POST /api/auth/register` — validate input, check email uniqueness, hash password, create user, issue JWT, set HttpOnly cookie, return user profile
- [ ] 3.3 Implement `POST /api/auth/login` — verify credentials, issue JWT, set HttpOnly cookie, return user profile
- [ ] 3.4 Implement `GET /api/auth/me` — return current authenticated user's profile
- [ ] 3.5 Implement `POST /api/auth/refresh` — verify refresh token, issue new access token
- [ ] 3.6 Implement `POST /api/auth/logout` — clear HttpOnly cookie
- [ ] 3.7 Implement `POST /api/auth/change-password` — verify current password, hash new password, increment `token_version`
- [ ] 3.8 Implement `POST /api/auth/logout-all` — increment `token_version` to invalidate all tokens
- [ ] 3.9 Register auth router in `main.py` (no auth dependency on `/api/auth/*` except `/me`)
- [ ] 3.10 Add CSRF `Origin` check middleware for POST/PATCH/DELETE requests in `main.py`

## 4. Retrofit Existing Routers with Auth

- [ ] 4.1 Add `Depends(get_current_user)` to all endpoints in `conversations.py` (8 endpoints); filter queries by `user_id`
- [ ] 4.2 Add auth to `messages.py` (5 endpoints); verify conversation ownership before any operation
- [ ] 4.3 Add auth to `agents.py` (5 endpoints); filter by `user_id IS NULL OR user_id = current` for queries, set `user_id` on create
- [ ] 4.4 Add auth to `artifacts.py` (4 endpoints); verify conversation ownership
- [ ] 4.5 Add auth to `attachments.py` (3 endpoints); verify conversation ownership
- [ ] 4.6 Add auth to `settings.py` (3 endpoints); transition from singleton `app_settings` to per-user `user_settings`
- [ ] 4.7 Add auth to `fs.py` (4 endpoints); verify conversation ownership, ensure workspace path includes user segment
- [ ] 4.8 Add auth to `documents.py` (5 endpoints); filter by `user_id`
- [ ] 4.9 Add auth to `memory.py` (multiple endpoints); filter by `user_id` scope
- [ ] 4.10 Add auth to `skills.py` (3 endpoints); filter by `user_id`
- [ ] 4.11 Add auth to `mcp.py` (5 endpoints); filter by `user_id`
- [ ] 4.12 Add auth to `pending.py` (multiple endpoints); verify conversation ownership
- [ ] 4.13 Add auth to `runs_misc.py` (2 endpoints); verify conversation ownership
- [ ] 4.14 Add auth to `deployments.py`; verify conversation ownership

## 5. SSE & EventBus Isolation

- [ ] 5.1 Modify `EventBus` to accept `user_id` on `subscribe()` and filter events by `user_id` before yielding to subscriber queue
- [ ] 5.2 Modify `EventBus.publish()` to tag each event with the owning `user_id` (system events like heartbeats use `user_id=None` = broadcast)
- [ ] 5.3 Modify `AgentRunner` / event publishing paths to include `user_id` when publishing events
- [ ] 5.4 Modify `stream.py` `/api/stream` endpoint to verify JWT (from cookie in production, from `?token=` query param in dev mode) before establishing SSE connection
- [ ] 5.5 Pass the authenticated `user_id` to `event_bus.subscribe(user_id)` in the SSE handler

## 6. Settings Service Refactor

- [ ] 6.1 Refactor `settings_service.py` — `get_app_settings()` → `get_user_settings(user_id)`, `update_app_settings()` → `update_user_settings(user_id, patch)`
- [ ] 6.2 Create `global_settings_service.py` — `get_global_settings()`, `update_global_settings()` for deployment config
- [ ] 6.3 Update `agent_runner.py` `build_adapter_input` to resolve API keys from `user_settings` (per-user) instead of singleton `app_settings`
- [ ] 6.4 Update `deploy_command_service.py` to read deployment config from `global_settings` instead of `app_settings`
- [ ] 6.5 Update all other callers of `get_app_settings()` / `settings_service` to pass `user_id`

## 7. Workspace & CLI Agent Isolation

- [ ] 7.1 Modify workspace path construction in `conversation_service.py` to include `users/{user_id}/workspaces/` segment
- [ ] 7.2 Update `worktree_service.py` to use user-scoped workspace root
- [ ] 7.3 Modify CLI agent subprocess spawning in `agent_runner.py` to set `HOME`/`USERPROFILE` env var to the user's directory
- [ ] 7.4 Update sandbox path validation in tool executor to enforce user-scoped root

## 8. RAG & Memory Isolation

- [ ] 8.1 Add `user_id` metadata field to Milvus collection schema; update `_wire_milvus_to_rag()` search/insert to include `user_id` filter
- [ ] 8.2 Add `user_id` field to Elasticsearch `rag_chunks` index mapping; update `_wire_es_to_rag()` search to include `user_id` filter
- [ ] 8.3 Update `RAGService` search/ingest methods to accept and propagate `user_id`
- [ ] 8.4 Update `MemoryService` (LTM, preference, graph) to filter by `user_id`
- [ ] 8.5 Update `PromptAssembler` sources (ProfileSource, RecallSource) to pass `user_id` to memory service

## 9. Mobile Companion Auth Migration

- [ ] 9.1 Replace `_require_mobile_auth()` in `mobile/routes.py` with `get_current_user` dependency
- [ ] 9.2 Keep legacy `AGENTHUB_MOBILE_TOKEN` support as fallback during transition (log deprecation warning)
- [ ] 9.3 Update mobile snapshot/detail queries to filter by authenticated `user_id`

## 10. Frontend Auth

- [ ] 10.1 Create `src/stores/auth-store.ts` — Zustand store with `user`, `isLoading`, `login()`, `register()`, `logout()`, `initialize()`, `refreshToken()`
- [ ] 10.2 Create `src/app/login/page.tsx` — email/password login form
- [ ] 10.3 Create `src/app/register/page.tsx` — email/name/password registration form (respect `ALLOW_REGISTRATION` from `/api/auth/me` config response)
- [ ] 10.4 Create `src/app/middleware.ts` or client-side route guard in `layout.tsx` — redirect unauthenticated users to `/login`
- [ ] 10.5 Create `authFetch` wrapper in `src/lib/api.ts` that injects `Authorization: Bearer` header from token mirror for cross-origin; handle 401 with auto-refresh + retry
- [ ] 10.6 Retrofit all existing `fetch()` calls in `src/lib/api.ts` to use `authFetch` (~50 calls)
- [ ] 10.7 Update `src/app/layout.tsx` — call `AuthStore.initialize()` on mount, show loading state while checking auth
- [ ] 10.8 Update `src/app/page.tsx` — render auth gate (redirect to `/login` if not authenticated)
- [ ] 10.9 Update `src/stores/app-store.ts` — add `userId` field populated from AuthStore
- [ ] 10.10 Update SSE connection in `StreamProvider` — pass token via cookie (same-origin) or `?token=` query param (cross-origin dev)

## 11. Config & Documentation

- [ ] 11.1 Update `backend/.env.example` with `JWT_SECRET`, `JWT_ACCESS_TOKEN_EXPIRY`, `JWT_REFRESH_TOKEN_EXPIRY`, `ALLOW_REGISTRATION`, `DEFAULT_USER_EMAIL`, `DEFAULT_USER_PASSWORD`
- [ ] 11.2 Update `CLAUDE.md` §2 (tech stack — add bcrypt, PyJWT) and §3.2 (core entities — add User as 8th entity)
- [ ] 11.3 Update `CLAUDE.md` §5 (security — add auth/JWT constraints, cookie CSRF, CLI HOME isolation)
- [ ] 11.4 Update `openspec/specs/` main specs to reflect multi-user changes after implementation

## 12. Testing

- [ ] 12.1 Write `backend/tests/test_auth.py` — register, login, refresh, me, logout, change-password, logout-all, duplicate email, invalid credentials, disabled registration
- [ ] 12.2 Write `backend/tests/test_auth_isolation.py` — two users, verify conversation/agent/document/settings isolation
- [ ] 12.3 Write `backend/tests/test_migration.py` — verify default user creation and back-fill on existing data
- [ ] 12.4 Write `backend/tests/test_sse_auth.py` — SSE connection without token (401), with valid token (events filtered by user)
- [ ] 12.5 Write `backend/tests/test_csrf.py` — mutation endpoint without matching Origin header (403)
- [ ] 12.6 Run `ruff check .` and `pytest` — ensure no regressions
- [ ] 12.7 Run `pnpm typecheck` and `pnpm lint` — ensure frontend changes pass
