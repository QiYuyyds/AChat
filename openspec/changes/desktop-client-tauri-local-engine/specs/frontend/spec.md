## ADDED Requirements

### Requirement: Frontend SHALL support desktop bridge integration without forking the product UI

The AChat frontend MUST remain a single product UI for web and desktop. Desktop-specific behavior MUST be gated on `window.achatDesktop` injection and MUST NOT require a separate business UI codebase.

#### Scenario: Same routes in desktop webview
- **WHEN** the packaged frontend loads inside the desktop shell after login
- **THEN** the user sees the same primary workspace routes as web
- **AND** desktop-only enhancements activate only when the bridge is present.

### Requirement: Frontend SHALL target the local engine for all business traffic in desktop mode

When desktop mode is active, auth, CRUD, settings, Agent execution, and streaming APIs MUST target the local engine base URL (or same-origin local host). The desktop client MUST NOT require a remote official AChat business API base URL for normal operation.

#### Scenario: Desktop message send uses local engine
- **WHEN** a logged-in desktop user sends a message that requires Agent execution
- **THEN** the request is issued to the local engine
- **AND** no remote official business API call is required for that send path.

#### Scenario: Desktop SSE targets local engine
- **WHEN** desktop mode is active and the global stream provider connects
- **THEN** EventSource targets the local engine `/api/stream`
- **AND** includes user and engine tokens as required by the engine
- **AND** does not rely on a remote official API process event bus for local Agent run streaming.

#### Scenario: Desktop SSE waits for bridge before connecting
- **WHEN** the frontend authenticates inside the Tauri shell before `window.achatDesktop` is injected
- **THEN** the stream provider MUST NOT permanently open EventSource against a non-engine base as if it were the local engine bus
- **AND** once the bridge (engineBaseUrl + engineToken) and access token are available, it MUST connect (or reconnect) to the local engine stream.

#### Scenario: Desktop REST and SSE stay coherent across loopback host aliases
- **WHEN** desktop UI runs on `localhost` and the injected engine base uses `127.0.0.1` (or the reverse) with the same port
- **THEN** `authFetch` / equivalent business REST clients attach the engine token for local engine targets
- **AND** StreamProvider uses a loopback-aligned engine base for `/api/stream`
- **AND** a valid logged-in session MUST NOT show “SSE connected” while conversation/agent list APIs systematically fail with engine-token 401 solely due to host alias mismatch.

#### Scenario: Desktop message history reads local engine
- **WHEN** desktop mode is active and the UI loads messages for a conversation
- **THEN** the client fetches message history from the local engine
- **AND** does not require a remote official business history API for the primary path.

### Requirement: Frontend SHALL provide a desktop static build artifact for packaging

The project MUST be able to produce frontend assets suitable for embedding in the desktop package and serving from the local engine (static export or equivalent offline-capable asset pipeline). Pure web `next dev` / server deployments MUST remain supported independently.

#### Scenario: Desktop package build includes UI assets
- **WHEN** maintainers run the documented desktop frontend build step
- **THEN** a directory of static assets is produced for inclusion under the desktop package resources
- **AND** those assets can be served without requiring a remote Next server at runtime.
