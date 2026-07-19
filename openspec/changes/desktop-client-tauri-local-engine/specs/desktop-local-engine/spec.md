## ADDED Requirements

### Requirement: Local engine SHALL run Agent loops on the user machine

In desktop mode, Agent runs (model turns, tool decisions, tool execution) MUST execute inside the local engine process on the user's Windows machine. The local engine MUST reuse AChat adapter and tool semantics rather than inventing a separate agent runtime.

#### Scenario: User sends a message in desktop mode while online
- **WHEN** an authenticated desktop user sends a chat message that starts an Agent run
- **THEN** the local engine performs the Agent loop
- **AND** filesystem and bash tools operate against the user's local workspace paths
- **AND** durable conversation state is written through the official cloud API when online.

### Requirement: Local engine SHALL bind only to loopback

The local engine HTTP server MUST listen on `127.0.0.1` only. It MUST NOT default to binding `0.0.0.0` or other non-loopback interfaces in desktop mode.

#### Scenario: Engine starts
- **WHEN** the local engine starts in desktop mode
- **THEN** its HTTP port is bound to 127.0.0.1
- **AND** remote LAN clients cannot reach the engine without explicit future product changes outside v1 scope.

### Requirement: Local engine SHALL authenticate desktop callers with engine token and official origin

Every non-health (or all, if health is token-gated by design) engine API request from the webview MUST include the session `engineToken`. The engine MUST reject requests with missing/invalid token. The engine MUST also validate `Origin` against the configured official frontend origin allowlist when an Origin header is present.

#### Scenario: Request without engine token
- **WHEN** a client calls a protected local engine endpoint without a valid engine token
- **THEN** the engine returns HTTP 401 or 403
- **AND** does not execute Agent or filesystem operations.

#### Scenario: Request from non-official Origin
- **WHEN** a browser page whose Origin is not in the allowlist calls the local engine with any token guess
- **THEN** the engine rejects the request.

### Requirement: Local engine SHALL NOT open direct database connections to cloud infrastructure

Desktop mode MUST access PostgreSQL, Milvus, Elasticsearch, Neo4j, and similar cloud infra only via the official HTTPS API surface. Connection strings for those services MUST NOT be required in the desktop package configuration for normal users.

#### Scenario: Online persistence
- **WHEN** the local engine needs to persist a conversation message while online
- **THEN** it calls the official cloud HTTP API
- **AND** does not open a direct Postgres client connection to the cloud database.

### Requirement: Local engine SHALL use local SQLite for offline continuation

When the official cloud API is unreachable after the user has an established local session context, the local engine MUST allow core chat/Agent continuation against a local SQLite store under the desktop data directory. On connectivity restore, the engine MUST attempt to upload offline-produced changes. v1 MUST NOT silently overwrite cloud data on conflict; it MUST surface a conflict/error state.

#### Scenario: Offline send after prior login material exists locally
- **WHEN** the cloud API is unreachable and the user continues a conversation on this machine
- **THEN** the local engine stores new activity in local SQLite
- **AND** Agent tools can still use local workspace files.

#### Scenario: Reconnect with conflicting cloud state
- **WHEN** offline local changes cannot be applied cleanly to the cloud authority
- **THEN** the engine or UI reports a conflict/sync failure
- **AND** does not silently discard cloud authority data without user-visible notice.

### Requirement: Local engine SHALL call model providers directly with user keys

Custom/OpenAI-compatible adapters in desktop mode MUST call model vendor APIs directly from the local engine using keys resolved for the authenticated user (fetched from cloud settings when online). v1 MUST NOT require a cloud model proxy.

#### Scenario: Custom agent run with user OpenAI-compatible key
- **WHEN** the user has saved a provider key in account settings and starts a custom agent
- **THEN** the local engine uses that key to call the vendor API directly
- **AND** does not send the completion request body through a mandatory cloud proxy hop.

### Requirement: Local engine SHALL detect CLI agents without bundling them

Claude Code and Codex CLI support MUST rely on CLIs already installed on the user machine. The package MUST NOT bundle those CLIs in v1. Missing CLIs MUST produce a clear, actionable error or UI guidance.

#### Scenario: Claude CLI missing
- **WHEN** the user starts a Claude CLI agent and `claude` is not available on PATH
- **THEN** the run fails with a clear message guiding installation
- **AND** the desktop app remains usable for non-CLI agents.

### Requirement: Local engine SHALL keep workspace files on the user machine

Workspace file contents for local/sandbox workspaces used by desktop runs MUST remain on the user's disk. Cloud APIs MAY store metadata and conversation references but MUST NOT be required to host the full project tree for local bound paths.

#### Scenario: Local folder binding
- **WHEN** the user binds a local directory as workspace
- **THEN** tool reads and writes apply to that directory on the user machine
- **AND** the directory is not uploaded wholesale to cloud object storage as a v1 requirement.

### Requirement: Local engine SHALL expose health and data-dir configuration

The engine MUST provide a health endpoint for the shell readiness probe and MUST accept a desktop data directory for logs, SQLite, and local runtime state.

#### Scenario: Shell readiness probe
- **WHEN** the shell requests the engine health endpoint with valid local auth as required by implementation
- **THEN** a successful response indicates the engine is ready to accept desktop traffic.

### Requirement: Local engine SSE SHALL use the same desktop user resolution as REST

Agent run events are published on the local engine's in-process event bus. Desktop clients MUST subscribe to `/api/stream` on the local engine (not only the official API process). User identity for that SSE endpoint MUST use desktop cloud JWT resolution (`resolve_desktop_user`), matching protected REST handlers, so that official access tokens work without sharing the official `JWT_SECRET` into the engine process.

#### Scenario: Send message then stream reply on local engine
- **WHEN** a desktop user successfully POSTs a message to the local engine and an Agent run publishes stream events for that user
- **AND** the frontend holds an EventSource to the local engine `/api/stream` with the official access token and engine token
- **THEN** the SSE connection authenticates as the same user as the POST
- **AND** the client receives the run's stream events (e.g. part deltas / message end) from the local bus.

### Requirement: Local engine message list SHALL mirror conversation context before ownership

Desktop online conversations are often created on the official API. Local engine SQLite may lack the conversation row until mirror runs. Both `GET` and `POST` `/api/conversations/{id}/messages` in desktop mode MUST call `ensure_conversation_context` (or equivalent mirror) before local ownership checks, so listing history for UI refresh works the same as send.

#### Scenario: Desktop GET messages for a cloud-created conversation
- **WHEN** a desktop client lists messages for a conversation id that exists on the official API but not yet in local engine DB
- **THEN** the engine mirrors conversation (and best-effort agents/messages) into local DB
- **AND** returns local message rows for that conversation after ownership succeeds
- **AND** does not fail solely because the local DB started empty for that id.
