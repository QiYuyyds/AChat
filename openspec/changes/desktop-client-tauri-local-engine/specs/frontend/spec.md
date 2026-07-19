## ADDED Requirements

### Requirement: Frontend SHALL support desktop bridge integration without forking the product UI

The AChat frontend MUST remain a single product UI for web and desktop. Desktop-specific behavior MUST be gated on `window.achatDesktop` injection and MUST NOT require a separate business UI codebase.

#### Scenario: Same routes in desktop webview
- **WHEN** the official frontend loads inside the desktop shell after login
- **THEN** the user sees the same primary workspace routes as web
- **AND** desktop-only enhancements activate only when the bridge is present.

### Requirement: Frontend SHALL route local execution calls to the engine in desktop mode

When desktop mode is active, APIs that start or stream local Agent execution MUST target the local engine base URL with the engine token, while account and cloud-authoritative CRUD remain on the official API base.

#### Scenario: Desktop message send uses local execution plane
- **WHEN** a logged-in desktop user sends a message that requires Agent execution
- **THEN** the execution request is issued to the local engine
- **AND** cloud auth credentials are still used for official cloud APIs as needed for persistence and settings.

#### Scenario: Desktop SSE targets local engine with user and engine tokens
- **WHEN** desktop mode is active and the global stream provider connects
- **THEN** EventSource targets the local engine base URL `/api/stream`
- **AND** includes the user access token (query) and engine token as required by the engine
- **AND** does not rely on the official API process event bus for local Agent run streaming.

#### Scenario: Desktop SSE waits for bridge before connecting
- **WHEN** the official frontend authenticates inside the Tauri shell before `window.achatDesktop` is injected
- **THEN** the stream provider MUST NOT permanently open EventSource against the official API base as if it were the local engine bus
- **AND** once the bridge (engineBaseUrl + engineToken) and access token are available, it MUST connect (or reconnect) to the local engine stream.

#### Scenario: Desktop message history prefers local engine
- **WHEN** desktop mode is active and the UI loads messages for a conversation that has run on the local engine
- **THEN** the client SHOULD fetch message history from the local engine when available
- **AND** fall back to the official API if the engine has no rows or the request fails.

#### Scenario: Desktop send falls back to engine history if SSE is late
- **WHEN** a desktop send returns `runIds` and stream events are delayed or missed
- **THEN** the client MAY re-fetch messages from the local engine after a short delay
- **AND** upsert returned agent messages into local UI state so the reply is visible without requiring a full page reload.
