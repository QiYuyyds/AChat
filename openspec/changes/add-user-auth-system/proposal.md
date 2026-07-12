# Add User Authentication System

## Why

AChat currently operates as a local single-user application with zero authentication — all 14 API routers are fully exposed, the database has no User table, and `app_settings` is a single-row table. To deploy AChat on a remote server (VPS / intranet) where multiple users access the same instance, we need user identity, authentication, and per-user data isolation. Without this, AChat cannot safely be exposed beyond `localhost`.

## What Changes

- **New `users` table** with email, name, bcrypt password hash, and timestamps
- **New auth API** (`/api/auth/register`, `/api/auth/login`, `/api/auth/me`, `/api/auth/refresh`) issuing JWT tokens
- **New auth middleware** — `get_current_user` FastAPI dependency injected into all 14 existing routers
- **BREAKING**: `app_settings` table transitions from single-row global config to per-user rows; global-only fields (deployment publish config) stay in a new `global_settings` table
- **BREAKING**: `agents`, `conversations`, `documents`, `mcp_servers` tables gain `user_id` foreign key for data isolation
- **BREAKING**: `long_term_memory`, `memory_nodes`, `chat_history` gain `user_id` for memory isolation
- **Frontend**: new login/register pages, `AuthStore` (Zustand), route guard, and `api.ts` retrofit with `Authorization: Bearer` header on every fetch
- **SSE authentication**: SSE connection switches from unauthenticated to cookie-based JWT verification (EventSource cannot set custom headers)
- **Mobile companion**: transitions from environment-variable pairing token to user JWT obtained via login
- **EventBus isolation**: events filtered by `user_id` before dispatching to SSE subscribers
- **RAG vector isolation**: Milvus collection and ES index gain `user_id` field for filtered search
- **Workspace path isolation**: workspace directory gains a `users/{user_id}/` prefix segment
- **Data migration script**: creates a default user and back-fills `user_id` on all existing rows

## Capabilities

### New Capabilities
- `user-auth`: User identity, registration, login (password + JWT), token refresh, and per-request authentication dependency. Covers password security requirements (bcrypt), JWT lifecycle (signing, verification, expiry, refresh), and the auth API surface.

### Modified Capabilities
- `core-domain`: New `User` entity added as the 8th core entity; existing entities (`Agent`, `Conversation`, `Document`) gain `user_id` association for ownership.
- `persistence`: Schema transitions from "local single-user storage" to "multi-user with per-user isolation"; `app_settings` splits into per-user `user_settings` + global `global_settings`; new `users` table; migration strategy for existing data.
- `platform-security`: New authentication and authorization security constraints; CLI agent subprocess environment isolation per user; workspace path isolation rules.
- `stream-events`: SSE endpoint requires authenticated cookie/JWT; events filtered by `user_id` before dispatch to subscribers.
- `mobile-companion`: Mobile pairing token replaced by user JWT login flow; mobile APIs use same auth dependency as desktop.
- `frontend`: New login/register pages, AuthStore, route guard, and authenticated API client layer.

## Impact

- **Backend new code**: `app/auth/` module (JWT, bcrypt, dependencies), `app/api/auth.py` router, migration script
- **Backend modified code**: all 14 existing routers (~55 endpoints) gain `Depends(get_current_user)`; `settings_service.py`, `conversation_service.py`, `agent_runner.py`, `event_bus.py` retrofitted for user context; `engine.py` migration statements
- **Database**: new `users` table, new `global_settings` table, `app_settings` → `user_settings` rename + `user_id` PK, `user_id` column added to 7+ tables, migration back-fill script
- **Frontend new code**: `src/app/login/page.tsx`, `src/stores/auth-store.ts`, `src/middleware.ts` (or client-side guard)
- **Frontend modified code**: `src/lib/api.ts` (auth header injection on ~50 fetch calls), `src/stores/app-store.ts` (user context), `src/app/layout.tsx` (auth initialization), `src/app/page.tsx` (auth gate)
- **Config**: new `jwt_secret`, `jwt_expiry`, `allow_registration` settings in `config.py` and `.env.example`
- **Dependencies**: `bcrypt`, `python-jose[cryptography]` (or `PyJWT`) added to `pyproject.toml`
- **Specs**: 6 spec files created/modified; `CLAUDE.md` §3.2 (core entities) and §5 (security) updated
