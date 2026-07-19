## ADDED Requirements

### Requirement: Local engine SHALL run as the full desktop business backend

In desktop mode, the local engine MUST host the complete AChat HTTP API surface required by the embedded frontend (auth, conversations, messages, settings, agents, stream, etc.), not only Agent execution helpers. Agent runs (model turns, tool decisions, tool execution) MUST execute inside the local engine process on the user's Windows machine.

#### Scenario: User sends a message in desktop mode while infrastructure is reachable
- **WHEN** an authenticated desktop user sends a chat message that starts an Agent run
- **THEN** the local engine performs the Agent loop
- **AND** filesystem and bash tools operate against the user's local workspace paths
- **AND** durable conversation state is written through the engine's normal persistence path against the configured primary store (remote PostgreSQL when online).

### Requirement: Local engine SHALL bind only to loopback

The local engine HTTP server MUST listen on `127.0.0.1` only. It MUST NOT default to binding `0.0.0.0` or other non-loopback interfaces in desktop mode.

#### Scenario: Engine starts
- **WHEN** the local engine starts in desktop mode
- **THEN** its HTTP port is bound to 127.0.0.1
- **AND** remote LAN clients cannot reach the engine without explicit future product changes outside v1 scope.

### Requirement: Local engine SHALL authenticate desktop callers with engine token and local UI origin

Every protected engine API request from the webview MUST include the session `engineToken`. The engine MUST reject requests with missing/invalid token. When an `Origin` header is present, the engine MUST validate it against the configured allowlist that includes the **local** UI origin (for example `http://127.0.0.1:<port>`).

#### Scenario: Request without engine token
- **WHEN** a client calls a protected local engine endpoint without a valid engine token
- **THEN** the engine returns HTTP 401 or 403
- **AND** does not execute Agent or filesystem operations.

#### Scenario: Request from non-allowlisted Origin
- **WHEN** a browser page whose Origin is not in the allowlist calls the local engine
- **THEN** the engine rejects the request.

### Requirement: Local engine SHALL connect to infrastructure directly using packaged defaults or user overrides

Desktop mode MUST load database and optional infra connection settings from packaged defaults and/or user-overridden config. The engine MUST open application-layer connections to PostgreSQL (and optional Milvus/ES/Neo4j per existing degrade rules) **directly**. Desktop mode MUST NOT require the official AChat **business** HTTPS API as a mandatory hop for normal online persistence.

#### Scenario: Online persistence with default official infra
- **WHEN** the local engine needs to persist a conversation message while the default remote PostgreSQL is reachable
- **THEN** it writes via the engine's SQLAlchemy/async DB path using the configured `DATABASE_URL` (or equivalent)
- **AND** does not require a call to a separate official AChat business API process for that write.

#### Scenario: User configures their own infrastructure
- **WHEN** the user saves custom infra connection settings in desktop settings
- **THEN** subsequent engine operations use the user override
- **AND** packaged defaults are not forced for that installation until the user reverts.

### Requirement: Local engine SHALL serve the packaged static frontend

The local engine MUST serve the embedded static frontend assets for the desktop UI origin (including SPA fallback to `index.html` as required by the frontend router).

#### Scenario: Shell navigates to engine origin
- **WHEN** the shell opens `http://127.0.0.1:<port>/` after health success
- **THEN** the engine returns the desktop frontend document
- **AND** static assets required by that UI are available from the same origin or a configured local origin.

### Requirement: Local engine SHALL use local SQLite for cache and offline continuation

When the primary remote store is unreachable after the user has an established local session context, the local engine MUST allow core chat/Agent continuation against a local SQLite store under the desktop data directory. On connectivity restore, the engine MUST attempt to sync offline-produced changes back to the primary store. v1 MUST NOT silently overwrite primary data on conflict; it MUST surface a conflict/error state.

#### Scenario: Offline send after prior login material exists locally
- **WHEN** the primary database is unreachable and the user continues a conversation on this machine
- **THEN** the local engine stores new activity in local SQLite
- **AND** Agent tools can still use local workspace files.

#### Scenario: Reconnect with conflicting primary state
- **WHEN** offline local changes cannot be applied cleanly to the primary store
- **THEN** the engine or UI reports a conflict/sync failure
- **AND** does not silently discard primary authority data without user-visible notice.

### Requirement: Local engine SHALL call model providers directly with user keys

Custom/OpenAI-compatible adapters in desktop mode MUST call model vendor APIs directly from the local engine using keys resolved for the authenticated user (from primary-store-backed user settings when available). v1 MUST NOT require a cloud model proxy. Claude/Codex CLI agents MUST use machine-local CLIs without bundling those CLIs in the package.

#### Scenario: Custom agent run with user OpenAI-compatible key
- **WHEN** the user has saved a provider key in account settings and starts a custom agent
- **THEN** the local engine uses that key to call the vendor API directly
- **AND** does not send the completion request body through a mandatory official business-API proxy hop.

#### Scenario: Claude CLI missing
- **WHEN** the user starts a Claude CLI agent and `claude` is not available on PATH
- **THEN** the run fails with a clear message guiding installation
- **AND** the desktop app remains usable for non-CLI agents.

### Requirement: Local engine SHALL keep workspace files on the user machine

Workspace file contents for local/sandbox workspaces used by desktop runs MUST remain on the user's disk.

#### Scenario: Local folder binding
- **WHEN** the user binds a local directory as workspace
- **THEN** tool reads and writes apply to that directory on the user machine
- **AND** the directory is not uploaded wholesale to remote object storage as a v1 requirement.

### Requirement: Local engine SHALL expose health and data-dir configuration

The engine MUST provide a health endpoint for the shell readiness probe and MUST accept a desktop data directory for logs, SQLite, config overrides, and local runtime state.

#### Scenario: Shell readiness probe
- **WHEN** the shell requests the engine health endpoint with valid local auth as required by implementation
- **THEN** a successful response indicates the engine is ready to accept desktop traffic.

### Requirement: Local engine SSE SHALL serve local in-process events for the authenticated desktop user

Agent run events are published on the local engine's in-process event bus. Desktop clients MUST subscribe to `/api/stream` on the local engine. User identity for that SSE endpoint MUST use the same auth resolution as protected REST on the local engine (local JWT/session for desktop v1). Engine-token middleware MUST still accept only the shell session.

#### Scenario: Send message then stream reply on local engine
- **WHEN** a desktop user successfully POSTs a message to the local engine and an Agent run publishes stream events for that user
- **AND** the frontend holds an EventSource to the local engine `/api/stream` with user and engine tokens as required
- **THEN** the SSE connection authenticates as the same user as the POST
- **AND** stream events for that run are delivered without requiring a remote official business API event bus.
