# Tasks: Remove Local SQLite User Filtering

## 1. Models & Schema

- [x] 1.1 Remove `user_id` column from `Agent` model in `backend/app/db/models.py`
- [x] 1.2 Remove `user_id` column from `Conversation` model in `backend/app/db/models.py`
- [x] 1.3 Remove `user_id` column from `McpServer` model in `backend/app/db/models.py`
- [x] 1.4 Remove `user_id` column from `ModelProfile` model in `backend/app/db/models.py`
- [x] 1.5 Remove `user_id` column from `Workspace` model in `backend/app/db/models.py` (if present)
- [x] 1.6 Update `ModelProfile.is_default` unique index from `(user_id) WHERE is_default = true` to `WHERE is_default = true`

## 2. Engine & Migration

- [x] 2.1 Remove local-table `ALTER TABLE ... ADD COLUMN user_id` statements from `_PG_MIGRATION_STATEMENTS` in `backend/app/db/engine.py`
- [x] 2.2 Remove local-table `ALTER TABLE ... ADD COLUMN user_id` statements from `_SQLITE_MIGRATION_STATEMENTS` in `backend/app/db/engine.py`
- [x] 2.3 Add `ALTER TABLE ... DROP COLUMN user_id` statements for all local tables to `_SQLITE_MIGRATION_STATEMENTS` (wrapped in `suppress(Exception)`)
- [x] 2.4 Add `ALTER TABLE ... DROP COLUMN user_id` statements for all local tables to `_PG_MIGRATION_STATEMENTS` (using `DROP COLUMN IF EXISTS`)
- [x] 2.5 Update `ModelProfile` default unique index migration to drop old `uq_model_profiles_default_per_user` and create new `uq_model_profiles_default` (`WHERE is_default = true` without `user_id`)
- [x] 2.6 Remove `get_local_db()` fallback to remote session factory (server mode is no longer supported)

## 3. API Layer — Remove user_id Filtering

- [x] 3.1 `backend/app/api/agents.py`: Remove `WHERE user_id IS NULL OR user_id = ?` filter from `list_agents`; remove `user_id=user.id` from `_create_custom_agent`
- [x] 3.2 `backend/app/api/model_profiles.py`: Remove `WHERE user_id = ?` from all CRUD operations; remove `user_id=user.id` from create; remove `user_id == user.id` from update/delete ownership checks
- [x] 3.3 `backend/app/api/conversations.py`: Pass-through (filtering is in `conversation_service`)
- [x] 3.4 `backend/app/api/mobile/routes.py`: Remove `Agent.user_id` filter from agent list query
- [x] 3.5 `backend/app/api/deployments.py`: Remove `Conversation.user_id` lookup from deployment query
- [x] 3.6 `backend/app/api/memory.py`: Remove `Conversation.user_id` filter from conversation stats query (line ~392)

## 4. Service Layer — Remove user_id Filtering

- [x] 4.1 `backend/app/services/conversation_service.py`: Remove `WHERE Conversation.user_id = ?` from `list_conversations`; remove `user_id` parameter from function signature
- [x] 4.2 `backend/app/services/conversation_service.py`: Remove `ModelProfile.user_id == agent.user_id` filter (lines ~466); query by `is_default` only
- [x] 4.3 `backend/app/services/agent_runner.py`: Update `_resolve_model_profile` — remove `user_id` parameter and filtering; query `WHERE is_default = true`
- [x] 4.4 `backend/app/services/agent_runner.py`: Remove `ModelProfile.user_id == agent.user_id` from auto-compact model limit lookup (line ~407)
- [x] 4.5 `backend/app/services/context_compaction_service.py`: Remove `ModelProfile.user_id == agent.user_id` filter (line ~518)
- [x] 4.6 `backend/app/services/plan_usage_service.py`: Remove `Conversation.user_id` filter from usage query (line ~28)
- [x] 4.7 `backend/app/services/workspace_env_service.py`: Remove `user_id=user_id` from all workspace creation calls
- [x] 4.8 `backend/app/services/worktree_service.py`: Remove `user_id=user_id` from worktree creation calls
- [x] 4.9 `backend/app/services/event_bus.py`: Remove `user_id` filtering from subscription matching (line ~71) — all local data is single-user

## 5. Auth & Ownership

- [x] 5.1 `backend/app/auth/ownership.py`: Simplify `verify_conversation_ownership` to existence check only (drop `user_id` comparison)
- [x] 5.2 `backend/app/auth/ownership.py`: Simplify `verify_artifact_ownership` to existence check only
- [x] 5.3 `backend/app/auth/ownership.py`: Simplify `verify_attachment_ownership` to existence check only
- [x] 5.4 `backend/app/auth/ownership.py`: Simplify `verify_agent_ownership` to existence check only (drop `user_id` and `is_builtin` logic)
- [x] 5.5 `backend/app/auth/ownership.py`: Keep `verify_document_ownership` unchanged (remote table, still needs `user_id` check)

## 6. Tools (Guide Agent Management Tools)

- [x] 6.1 `backend/app/tools/manage_agents.py`: Remove `Agent.user_id` filter from list query; remove `user_id=user_id` from agent creation
- [x] 6.2 `backend/app/tools/manage_mcp.py`: Remove `McpServer.user_id` filter from list query; remove `user_id=user_id` from MCP server creation
- [x] 6.3 `backend/app/tools/manage_documents.py`: Keep `user_id` (documents are remote table — unchanged)
- [x] 6.4 `backend/app/tools/manage_memory.py`: Keep `user_id` (long_term_memory is remote table — unchanged)
- [x] 6.5 `backend/app/tools/manage_profile.py`: Keep `user_id` (user_preferences is remote table — unchanged)

## 7. Main.py Migration Functions

- [x] 7.1 `backend/app/main.py`: Update `_migrate_agent_model_profiles` — remove `ModelProfile.user_id` filter from dedup check and profile creation; use `is_default` without `user_id`
- [x] 7.2 `backend/app/main.py`: Remove `user_id` from any other ModelProfile-related queries in startup

## 8. Migration Script Update

- [x] 8.1 `backend/scripts/migrate_to_multi_user.py`: Remove local-table back-fill code (Agent, Conversation, McpServer)
- [x] 8.2 `backend/scripts/migrate_to_multi_user.py`: Keep remote-table back-fill (Document, long_term_memory, memory_nodes, chat_history, rag_chunks, user_preferences)
- [x] 8.3 `backend/scripts/migrate_to_multi_user.py`: Remove NOT NULL constraint enforcement on local tables (conversations, documents, mcp_servers) — only keep for remote tables

## 9. Frontend (Minimal)

- [x] 9.1 Check `src/stores/app-store.ts` and other stores — remove any `userId` fields sent in request bodies for local-table CRUD (agents, conversations, model_profiles)
- [x] 9.2 Check `src/shared/types.ts` — remove `userId` from local-entity type definitions if present (Agent, Conversation, ModelProfile)
- [x] 9.3 Run `pnpm typecheck` to verify no type errors

## 10. Specs & Documentation

- [x] 10.1 Update `specs/08-db-schema.md` — remove `user_id` from local entity field definitions
- [x] 10.2 Update `specs/01-core-entities.md` — remove `user_id` from Agent, Conversation, McpServer field definitions
- [x] 10.3 Update `CLAUDE.md` §3.2 — remove mention of `user_id` FK on local entities
- [x] 10.4 Update `CLAUDE.md` §5.5 — clarify that data isolation applies to remote (PG) tables only

## 11. Verification

- [x] 11.1 Run `ruff check .` — fix any lint errors
- [x] 11.2 Run `pytest` — fix any test failures related to `user_id` on local tables
- [x] 11.3 Update tests that create local-table rows with `user_id` — remove `user_id` from test fixtures
- [ ] 11.4 Manual test: start backend, verify agents/conversations/model_profiles load correctly after PG reset (the original bug scenario)
