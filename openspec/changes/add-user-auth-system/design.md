# Design: User Authentication System

## Context

AChat is a multi-agent collaboration platform currently architected as a local single-user application. The backend (FastAPI :8000) has 14 routers with ~55 endpoints, none of which require authentication. The database (PostgreSQL via SQLAlchemy async) has 17 tables with no `User` entity. The frontend (Next.js :3000) renders the main workspace directly with no login gate. The `app_settings` table is a single-row singleton storing global API keys.

The system needs to support remote deployment where multiple users access the same AChat instance. This requires: user identity, authentication, per-user data isolation, and secure SSE/EventBus channels.

Reference implementation: `待融合项目/multica-main` has a complete Go + React auth system (email verification + Google OAuth + JWT). We adapt its User schema concept but implement with Python ecosystem (bcrypt + PyJWT) and start with password-only auth.

## Goals / Non-Goals

**Goals:**
- Users can register and log in with email + password
- JWT-based authentication on all API endpoints
- Per-user data isolation: conversations, agents, documents, settings, memory, RAG
- SSE stream authenticated and filtered by user
- Mobile companion uses the same JWT auth
- Existing data migrated to a default user without loss
- Workspace filesystem paths isolated per user

**Non-Goals:**
- OAuth (Google/GitHub) — deferred to a follow-up change; the JWT issuance layer is designed to accommodate it
- Email verification / SMTP — deferred; passwords are sufficient for remote access
- Team/workspace collaboration (multi-tenant) — this change is single-tenant multi-user
- Role-based access control (RBAC) — all authenticated users have equal privileges
- Rate limiting on auth endpoints — deferred to a separate security hardening change

## Decisions

### D1: Password auth with bcrypt + JWT (not OAuth, not email codes)

**Choice**: Email + password with bcrypt hash, JWT for session tokens.

**Rationale**: Zero external dependencies (no SMTP, no OAuth provider). Python `bcrypt` + `PyJWT` are mature standard libraries. The JWT signing/verification layer is decoupled from the credential source, so adding OAuth later only requires a new `/api/auth/oauth/<provider>` endpoint that issues the same JWT.

**Alternatives considered**:
- Email verification codes (multica-main approach): requires SMTP service, adds latency, overkill for intranet/VPS deployment
- OAuth only: requires provider registration, won't work for air-gapped intranets
- PIN/passphrase: too weak for remote access

### D2: JWT in HttpOnly cookie (not localStorage)

**Choice**: JWT stored in an HttpOnly, SameSite=Lax cookie.

**Rationale**: 
- EventSource (SSE) cannot set custom `Authorization` headers — cookies are sent automatically by the browser
- HttpOnly prevents JavaScript access, mitigating XSS token theft
- SameSite=Lax provides CSRF protection for non-GET requests (combined with `SameSite` check on mutation endpoints)

**Alternatives considered**:
- localStorage + Authorization header: XSS-vulnerable, breaks SSE (requires URL query param fallback which leaks tokens in logs)
- localStorage + SSE token-in-URL: token appears in browser history and server logs

**CSRF mitigation**: All mutation endpoints (POST/PATCH/DELETE) check `Origin` or `Referer` header against allowed origins. GET requests are idempotent and don't need CSRF protection.

### D3: `app_settings` splits into `user_settings` + `global_settings`

**Choice**: 
- New `user_settings` table (PK = `user_id`) stores per-user API keys, companion mode, mobile token
- New `global_settings` table (single row, PK = `'singleton'`) stores deployment publish config (shared across users)
- Existing `AppSettings` model renamed to `UserSettings`, fields repartitioned

**Rationale**: API keys are per-user (each user brings their own Anthropic/OpenAI key). Deployment publish config is server-level (directory path, base URL) and shared. Splitting avoids forcing every user to configure deployment paths.

**Alternatives considered**:
- Keep `app_settings` as single-row global, add separate `user_api_keys` table: fragments key resolution logic across two tables
- Make all settings per-user: deployment config needlessly duplicated

### D4: Indirect isolation via `conversations.user_id` (not per-table user_id)

**Choice**: Only top-level ownership tables get `user_id`:
- `agents` (user_id, NULL for builtin)
- `conversations` (user_id, NOT NULL)
- `documents` (user_id, NOT NULL)
- `mcp_servers` (user_id, NOT NULL)
- `user_settings` (user_id as PK)
- `long_term_memory` (user_id, extending existing scope)
- `memory_nodes` (user_id)
- `chat_history` (user_id)

Child tables (`messages`, `artifacts`, `workspaces`, `attachments`, `agent_runs`, `context_summaries`, `checkpoints`, `rag_chunks`) are isolated indirectly through their parent `conversation_id` or `document_id` foreign key chain.

**Rationale**: Avoids redundant columns and synchronization cost. The conversation is the aggregate root — if a user owns the conversation, they own everything cascading from it.

**Alternatives considered**:
- Add `user_id` to every table: maximum query flexibility but 8+ extra columns, each needing index + sync on user transfer
- Row-level security (RLS) in PostgreSQL: powerful but adds operational complexity and couples to PG-specific features

### D5: Builtin agents shared via `user_id = NULL`

**Choice**: `agents.user_id` is nullable. `is_builtin = true` agents have `user_id = NULL` and are visible to all users. Custom agents have `user_id = <owner>`.

**Rationale**: Builtin agents (Claude Code, Codex, etc.) are platform defaults that every user needs. Duplicating them per user wastes storage and complicates updates.

**Query pattern**: `WHERE agents.user_id = :current_user_id OR agents.user_id IS NULL`

### D6: EventBus subscription filtered by user_id

**Choice**: `event_bus.subscribe()` accepts a `user_id` parameter. Events are tagged with `user_id` when published. The subscriber loop filters events before yielding to the SSE queue.

**Rationale**: The current EventBus is a global broadcast — all subscribers receive all events. In multi-user mode, each SSE connection must only see events for its user. Filtering at the subscriber level (not publisher) keeps the publish path simple.

### D7: RAG vector isolation via metadata field (not per-user collections)

**Choice**: Add `user_id` as a field in the Milvus collection schema and ES index mapping. Search queries include a `user_id` filter expression.

**Rationale**: Creating per-user Milvus collections (or ES indices) would exhaust resources at scale (each collection has fixed overhead). A filter field is cheaper and simpler to manage.

**Milvus**: `filter="user_id == '<uid>'"` in search call
**ES**: `"filter": [{"term": {"user_id": "<uid>"}}]` in query body

### D8: Workspace path gains user segment

**Choice**: Workspace directory structure changes from:
```
.agenthub-data/workspaces/conv_xxx/
```
to:
```
.agenthub-data/users/{user_id}/workspaces/conv_xxx/
```

**Rationale**: Filesystem-level isolation prevents path traversal across users. The `Workspace.root_path` column stores the absolute path, so this is transparent to the workspace service as long as path construction uses the user-scoped root.

### D9: Migration creates a default user

**Choice**: The migration script creates a single `default_user` (email from env `DEFAULT_USER_EMAIL` or `admin@local`) and back-fills `user_id` on all existing rows.

**Rationale**: Existing single-user deployments have data that belongs to nobody. Creating a default user preserves all data and lets the operator log in as that user to continue working.

### D10: `allow_registration` environment variable

**Choice**: New `ALLOW_REGISTRATION=true|false` env var (default `true` for new deployments, `false` during migration).

**Rationale**: Operators deploying to shared intranets may want to control who can register. This is a simple gate, not a full invitation system.

## Risks / Trade-offs

- **[Breaking change for existing deployments]** → Migration script is idempotent and reversible (default user back-fill). Document the migration in `backend/scripts/` with a rollback path (drop `user_id` columns, restore `app_settings` singleton).
- **[Cookie CSRF on mutation endpoints]** → All POST/PATCH/DELETE endpoints check `Origin` header against `cors_origins_list`. This is sufficient for SameSite=Lax cookies.
- **[JWT expiry vs UX]** → Access token expires in 1 hour; refresh token in 7 days. Frontend `AuthStore.initialize()` silently refreshes on load. If refresh fails, redirect to `/login`.
- **[CLI agent HOME isolation]** → Claude Code / Codex CLI subprocesses use `HOME`/`USERPROFILE` for config. Multi-user mode needs per-user HOME override in subprocess env. Risk: CLI tools may not respect overridden HOME for all paths. Mitigation: test with Claude Code and Codex explicitly.
- **[Milvus/ES user_id back-fill]** → Existing RAG chunks lack `user_id`. Migration sets them all to `default_user`. New chunks get the correct user. Stale chunks from other users won't appear in search (filtered out).
- **[SSE cookie cross-origin]** → If frontend and backend are on different origins (e.g., `localhost:3000` and `localhost:8000`), cookies need `SameSite=None; Secure`. In dev (HTTP), this won't work. Mitigation: in dev mode, fall back to `?token=` query param for SSE; in production (HTTPS), use cookies.
- **[50+ fetch calls in api.ts need auth headers]** → Instead of editing each call, wrap `fetch` in an `authFetch` helper that injects the cookie automatically (cookies are auto-sent for same-origin). For cross-origin, the helper reads JWT from a JS-accessible mirror and adds `Authorization` header. This minimizes the diff.

## Migration Plan

1. **Pre-migration**: Backup PostgreSQL database
2. **Run migration script** (`backend/scripts/migrate_to_multi_user.py`):
   - Create `users` table
   - Insert default user (email from `DEFAULT_USER_EMAIL` env or `admin@local`, password from `DEFAULT_USER_PASSWORD` env or randomly generated and printed to stdout)
   - Add `user_id` columns to 8 tables (nullable first)
   - Back-fill `user_id = default_user.id` on all existing rows
   - Set columns to NOT NULL (where applicable)
   - Create `global_settings` table, copy deployment config from `app_settings`
   - Rename `app_settings` to `user_settings`, add `user_id` PK
   - Add `user_id` field to Milvus collection schema and ES index mapping
3. **Update backend code**: deploy new version with auth middleware
4. **Update frontend**: deploy new version with login page
5. **Verify**: log in as default user, confirm all data is accessible
6. **Rollback** (if needed): restore DB backup, redeploy old version

## Open Questions

- **Token rotation on password change?** Recommended yes — all existing JWTs invalidated when password changes. Implementation: store a `token_version` field on User, increment on password change, verify in JWT validation.
- **Admin panel for user management?** Not in scope for this change. Operators can manage users via SQL or a future admin change.
- **Session revocation (logout all devices)?** The `token_version` field above enables this. Logout-all = increment `token_version`.
