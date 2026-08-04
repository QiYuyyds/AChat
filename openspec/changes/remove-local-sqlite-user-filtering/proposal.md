# Proposal: Remove Local SQLite User Filtering

## Why

Dual-DB mode is the only deployment mode (one person, one machine, one user). Local SQLite tables (`agents`, `conversations`, `model_profiles`, `mcp_servers`, etc.) still carry `user_id` filtering inherited from the multi-user PG model. When PostgreSQL is reset, the same email gets a new `user_id` (nanoid), orphaning all local data — agents, conversations, and model profiles become invisible with no recovery path.

## What Changes

- **BREAKING**: Remove `user_id` filtering from all `get_local_db()` queries (agents, conversations, model_profiles, mcp_servers, messages, artifacts, workspaces, etc.)
- **BREAKING**: Remove `user_id` assignment from all local-table create operations (agents, conversations, model_profiles, mcp_servers, workspaces)
- Remove `user_id` ownership checks (`auth/ownership.py`) for local tables; keep auth (JWT still required for API access)
- Remove `user_id` migration/back-fill logic for local tables from `migrate_to_multi_user.py` and `engine.py` migration statements
- Remove `user_id` column from local table definitions in `models.py` (agents, conversations, mcp_servers, model_profiles, workspaces, messages, artifacts, agent_runs, etc.)
- Remote (PG) tables retain full `user_id` isolation (users, user_settings, documents, rag_chunks, long_term_memory, etc.)
- Add a one-time data migration script to reassign orphaned local rows to the current default user before stripping the column, so no data is lost

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `persistence`: Local SQLite tables no longer carry `user_id` columns or filtering. Table routing, dual-DB engine setup, and remote-table isolation remain unchanged. Migration statements that add/maintain `user_id` on local tables are removed.
- `user-auth`: Data isolation requirements scoped to local tables are removed. Authentication (JWT login, registration, token lifecycle) remains unchanged. Remote-table data isolation (user_settings, documents, RAG, memory) remains unchanged.

## Impact

- **Backend code**: `backend/app/api/agents.py`, `backend/app/api/conversations.py`, `backend/app/api/model_profiles.py`, `backend/app/api/mobile/routes.py`, `backend/app/services/conversation_service.py`, `backend/app/services/agent_runner.py`, `backend/app/services/plan_usage_service.py`, `backend/app/services/workspace_env_service.py`, `backend/app/services/worktree_service.py`, `backend/app/auth/ownership.py`, `backend/app/tools/manage_agents.py`, `backend/app/tools/manage_mcp.py`, `backend/app/db/models.py`, `backend/app/db/engine.py`, `backend/app/db/table_routing.py`, `backend/scripts/migrate_to_multi_user.py`
- **Frontend**: Minimal — the API response shapes don't change; `user_id` is not consumed by frontend stores for local-table entities
- **Database**: SQLite local tables lose `user_id` columns (ALTER TABLE DROP COLUMN or table recreate); PG migration statements for local-table `user_id` columns are removed
- **Specs**: `specs/08-db-schema.md`, `specs/01-core-entities.md` — remove `user_id` from local entity field definitions
- **Security**: No regression — authentication still required for all API endpoints; remote-table isolation (the real multi-user boundary) is untouched
