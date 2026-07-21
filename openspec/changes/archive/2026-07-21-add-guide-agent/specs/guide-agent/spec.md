# Guide Agent

## Purpose

Defines 小A Agent — a builtin management guide agent with a global floating panel UX, 7 management tools, LLM-driven memory optimization, and dual-active-conversation model. This is a new capability.

## ADDED Requirements

### Requirement: Guide agent SHALL be a builtin management-only agent

AChat SHALL seed a builtin agent with `is_builtin=true` and `is_guide=true` at backend startup. The guide agent SHALL have `user_id=NULL` (globally shared), `adapter_name='custom'`, and a fixed id `ag_guide_builtin`. The guide agent SHALL NOT own baseline work tools (`fs_*` / `bash` / `write_artifact` etc.) — it only owns the 7 management tools plus `ask_user`. The guide agent's system prompt SHALL constrain its behavior to management only: no code writing, no file editing, no command execution, no artifact production, no task dispatch.

#### Scenario: Backend starts for the first time
- **WHEN** the backend lifespan startup runs and no agent with `is_guide=true` exists
- **THEN** AChat creates the guide agent with id `ag_guide_builtin`, name `小A`, avatar `🅰️`, `adapter_name='custom'`, `model_provider` / `model_id` / `api_key` / `api_base_url` read from `GUIDE_AGENT_MODEL_PROVIDER` / `GUIDE_AGENT_MODEL_ID` / `GUIDE_AGENT_API_KEY` / `GUIDE_AGENT_API_BASE_URL` env vars (defaults: `deepseek` / `deepseek-v4-flash` / NULL / NULL), and the 7 management tools in `tool_names`
- **AND** the guide agent is visible to all users (because `user_id IS NULL`).
- **AND** users can switch the guide agent to any OpenAI-compatible provider (e.g. LongCat) by setting the env vars before first startup, or by `PATCH /api/agents/ag_guide_builtin` afterwards.

#### Scenario: Backend restarts with existing guide agent
- **WHEN** the backend lifespan startup runs and an agent with `is_guide=true` already exists
- **THEN** AChat does NOT create a duplicate guide agent
- **AND** the existing guide agent record is left unchanged.

#### Scenario: Guide agent seed fails
- **WHEN** the seed mechanism raises an exception
- **THEN** AChat logs a warning and continues startup without blocking
- **AND** the guide agent is absent until the next successful startup.

### Requirement: Guide agent SHALL skip baseline tool merging

AgentRunner SHALL NOT merge baseline tools (`read_attachment` / `ask_user` / `fs_list` / `fs_read` / `fs_write` / `fs_grep` / `fs_glob` / `bash`) into the guide agent's tool set. The guide agent's effective tools SHALL be its configured `tool_names` (the 7 management tools) plus `ask_user` (for confirmation). The `ask_user` tool SHALL be explicitly injected because the guide agent skips baseline merging.

#### Scenario: Guide agent runs a task
- **WHEN** AgentRunner assembles the tool list for a guide agent
- **THEN** the tool list contains only the 7 management tools and `ask_user`
- **AND** does NOT contain `fs_*`, `bash`, `write_artifact`, or other work tools.

#### Scenario: Non-guide custom agent runs a task
- **WHEN** AgentRunner assembles the tool list for a regular custom agent (`is_guide=false`)
- **THEN** baseline tools ARE merged as before
- **AND** the behavior is unchanged from pre-change.

### Requirement: Management tools SHALL be guide-agent-only

The 7 management tools (`manage_agents` / `manage_skills` / `manage_mcp` / `manage_documents` / `manage_memory` / `manage_profile` / `manage_conversations`) SHALL be registered in the tool registry but MUST only be injected into guide agents. AgentRunner SHALL NOT inject management tools into non-guide agents, even if `tool_names` mistakenly lists them.

#### Scenario: Regular agent has manage_agents in tool_names
- **WHEN** a non-guide agent's `tool_names` includes `manage_agents`
- **THEN** AgentRunner filters it out during tool injection
- **AND** the agent does NOT have access to management tools at runtime.

#### Scenario: Guide agent tool injection
- **WHEN** AgentRunner assembles tools for a guide agent
- **THEN** all 7 management tools are injected (resolved from registry)
- **AND** `ask_user` is also injected for confirmation flows.

### Requirement: Guide agent SHALL use user_id isolation

All management tools SHALL use `ToolContext.user_id` to scope operations. A guide agent SHALL NOT read, modify, or delete another user's data. Management tool handlers SHALL reject operations on builtin agents (except `list` and read-only `get` actions).

#### Scenario: Guide agent lists agents for the current user
- **WHEN** the guide agent calls `manage_agents(action=list)`
- **THEN** the result includes builtin agents (`user_id IS NULL`) and only the current user's custom agents
- **AND** excludes other users' custom agents.

#### Scenario: Guide agent attempts to delete a builtin agent
- **WHEN** the guide agent calls `manage_agents(action=delete, agent_id=<builtin_id>)`
- **THEN** the tool handler rejects the operation
- **AND** returns an error stating builtin agents cannot be deleted.

### Requirement: Destructive operations SHALL require confirmation

The guide agent's system prompt SHALL require using `ask_user` before destructive operations (delete any resource, modify API Key, batch memory optimization). Management tool handlers SHALL enforce a `confirm` parameter for `delete` actions: if `confirm != true`, the handler returns an error instructing the LLM to confirm via `ask_user` first.

#### Scenario: Guide agent deletes an agent without confirmation
- **WHEN** the guide agent calls `manage_agents(action=delete, agent_id=X, confirm=false)`
- **THEN** the tool handler returns an error: "删除操作需要先通过 ask_user 向用户确认，并传 confirm=true"
- **AND** the deletion does NOT occur.

#### Scenario: Guide agent deletes an agent with confirmation
- **WHEN** the guide agent calls `manage_agents(action=delete, agent_id=X, confirm=true)`
- **AND** agent X is a non-builtin agent owned by the current user
- **THEN** the tool handler deletes agent X
- **AND** returns a success summary.

### Requirement: Guide conversation SHALL be a separate mode

AChat SHALL support `Conversation.mode='guide'` as a new conversation mode. Guide conversations SHALL NOT appear in `list_conversations` results. Guide conversations SHALL NOT be deletable by users. Each user SHALL have at most one guide conversation (singleton, identified by `guideConversationId` in the frontend store).

#### Scenario: User's conversation list excludes guide conversations
- **WHEN** the frontend calls `GET /api/conversations`
- **THEN** the response excludes all conversations where `mode='guide'`
- **AND** only `single` and `group` conversations are returned.

#### Scenario: User attempts to delete a guide conversation
- **WHEN** a delete request targets a conversation with `mode='guide'`
- **THEN** the backend rejects the deletion
- **AND** returns an error stating guide conversations cannot be deleted.

#### Scenario: Guide conversation is created
- **WHEN** the frontend calls `POST /api/conversations` with `mode='guide'` and `agentIds=['ag_guide_builtin']`
- **THEN** the backend creates a conversation with `mode='guide'`, an empty sandbox workspace, and `user_id` of the creating user
- **AND** the conversation does NOT appear in the sidebar conversation list.

### Requirement: Guide agent SHALL support LLM-driven memory optimization

The `manage_memory` tool SHALL support an `optimize` action that accepts a `plan` with `delete_ids`, `merge_groups`, and `update_ids`. The guide agent's LLM SHALL analyze memories and generate the plan; the tool handler SHALL only execute the plan (delete + create-with-embedding + update). The plan MUST be confirmed by the user via `ask_user` before execution. This is distinct from the algorithm-driven `consolidate()` which runs automatically at a 0.95 similarity threshold.

#### Scenario: Guide agent proposes a memory optimization plan
- **WHEN** the user asks to clean up memories
- **THEN** the guide agent calls `manage_memory(action=list)` to fetch all memories
- **AND** analyzes each memory to identify垃圾/重复/分散/低价值
- **AND** generates a plan with `delete_ids`, `merge_groups`, and `update_ids`
- **AND** presents the plan to the user via `ask_user` for confirmation.

#### Scenario: User confirms and the plan is executed
- **WHEN** the user confirms the plan
- **THEN** the guide agent calls `manage_memory(action=optimize, plan=...)`
- **AND** the tool handler deletes the `delete_ids` and each `merge_groups.source_ids`
- **AND** creates new memories for each `merge_groups` with `merged_content` (generating embeddings via `long_term.add()`)
- **AND** updates attributes for each `update_ids`
- **AND** returns a summary: deleted N, merged M groups, updated K, net reduced X.

#### Scenario: Merge produces a memory without embedding (embedding service down)
- **WHEN** the embedding service is unavailable during `optimize` execution
- **THEN** the new merged memory is created without an embedding
- **AND** the tool handler logs a warning
- **AND** the memory is still searchable by text but not by vector recall.

### Requirement: Guide floating panel SHALL coexist with work conversations

The frontend SHALL support a dual-active-conversation model: `activeConversationId` (work conversation) and `guideConversationId` (guide conversation) can both be active simultaneously. The guide floating panel SHALL render independently of the main chat panel, with its own message list and input. SSE events SHALL be applied to the correct conversation by `conversationId` bucketing. The floating panel SHALL NOT change `activeConversationId`.

#### Scenario: User works and chats with guide simultaneously
- **WHEN** the user has a work conversation active in the main panel
- **AND** the guide floating panel is open
- **THEN** messages in the work conversation render in the main panel
- **AND** messages in the guide conversation render in the floating panel
- **AND** neither panel interferes with the other's `activeConversationId`.

#### Scenario: Guide floating panel state persists
- **WHEN** the user drags, resizes, or collapses the floating panel
- **THEN** the position, size, and open state are persisted to `localStorage` (per-user)
- **AND** restored on next page load.

#### Scenario: Keyboard shortcut toggles panel
- **WHEN** the user presses `Ctrl/Cmd + G`
- **THEN** the guide floating panel toggles between expanded and collapsed states.

### Requirement: Guide side effects SHALL notify frontend panels

When a management tool successfully executes a create/update/delete/refresh operation, the backend SHALL emit a `guide_side_effect` SSE event with a `target` field (`agents` / `skills` / `mcp` / `documents` / `memory` / `profile` / `conversations`). The frontend SHALL refresh the corresponding panel upon receiving this event.

#### Scenario: Guide agent creates a new agent
- **WHEN** `manage_agents(action=create)` succeeds
- **THEN** the backend emits `guide_side_effect` with `target='agents'`, `action='create'`
- **AND** the frontend re-fetches the agents list to show the new agent in the sidebar.

#### Scenario: Guide agent deletes a memory
- **WHEN** `manage_memory(action=delete)` succeeds
- **THEN** the backend emits `guide_side_effect` with `target='memory'`, `action='delete'`
- **AND** the frontend refreshes the memory panel.

### Requirement: Guide agent LLM SHALL be configurable via environment variables

The guide agent's `model_provider` / `model_id` / `api_key` / `api_base_url` SHALL be read from `GUIDE_AGENT_MODEL_PROVIDER` / `GUIDE_AGENT_MODEL_ID` / `GUIDE_AGENT_API_KEY` / `GUIDE_AGENT_API_BASE_URL` env vars at seed time, with defaults of `deepseek` / `deepseek-v4-flash` / NULL / NULL for backward compat. This lets users pick any OpenAI-compatible backend (DeepSeek, LongCat, etc.) without code changes. The project SHALL document these vars in `.env.example`. For the `deepseek` default provider, when `GUIDE_AGENT_API_KEY` is NULL, `get_effective_api_key` falls through to `DEEPSEEK_API_KEY` then `user_settings.deepseek_api_key`. For `openai-compatible` providers (e.g. LongCat), `GUIDE_AGENT_API_KEY` is required because there is no dedicated env-var fallback slot.

#### Scenario: Guide agent uses DeepSeek (default)
- **WHEN** `GUIDE_AGENT_*` env vars are unset
- **THEN** the seed creates the guide agent with `model_provider='deepseek'`, `model_id='deepseek-v4-flash'`, `api_key=NULL`
- **AND** at runtime the key resolves via the three-layer chain (agent → user_settings → `DEEPSEEK_API_KEY` env).

#### Scenario: Guide agent uses LongCat
- **WHEN** `GUIDE_AGENT_MODEL_PROVIDER=openai-compatible`, `GUIDE_AGENT_MODEL_ID=LongCat-2.0`, `GUIDE_AGENT_API_KEY=ak_xxx`, `GUIDE_AGENT_API_BASE_URL=https://api.longcat.chat/openai`
- **THEN** the seed creates the guide agent with those values
- **AND** at runtime the adapter uses the per-agent key directly (openai-compatible has no env fallback).

#### Scenario: Existing guide agent is reconfigured
- **WHEN** the guide agent already exists and the user wants to switch provider
- **THEN** the user sends `PATCH /api/agents/ag_guide_builtin` with the new `modelProvider` / `modelId` / `apiKey` / `apiBaseUrl`
- **AND** the backend applies the update (builtin agents may be reconfigured; only deletion is protected).
- **AND** the env vars are NOT re-read (they only apply at seed time).
