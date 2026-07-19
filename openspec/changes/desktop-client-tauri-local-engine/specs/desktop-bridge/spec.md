## ADDED Requirements

### Requirement: Frontend SHALL detect desktop mode only via shell injection

The frontend MUST treat the session as desktop mode when `window.achatDesktop?.isDesktop === true`. Absence of the injection MUST keep pure web behavior. The frontend MUST NOT permanently switch to desktop mode solely because an arbitrary localhost port responds.

#### Scenario: Browser visit without injection
- **WHEN** a user opens the web frontend in a normal browser without Tauri injection
- **THEN** desktop-only bridge APIs are not required
- **AND** the app behaves as the existing web client.

#### Scenario: Desktop webview with injection
- **WHEN** the page runs inside the desktop shell with `window.achatDesktop` injected
- **THEN** the frontend enables desktop engine integration paths.

### Requirement: Frontend SHALL call the local engine for all business APIs in desktop mode

In desktop mode, REST and SSE business APIs (including auth, conversations, settings, agents, messages, and stream) MUST target the local engine base URL (or same-origin local UI host that reverse-proxies to it). Requests to the local engine MUST include the session engine token using the agreed header (for example `X-Engine-Token`) when required by the engine. The frontend MUST obtain the token from `window.achatDesktop`, not from user-typed input.

#### Scenario: Desktop starts an agent run on the local engine
- **WHEN** the desktop frontend invokes a business or execution API
- **THEN** the request targets `engineBaseUrl` (or same-origin local host)
- **AND** includes the injected engine token when required.

#### Scenario: Desktop login stays on local engine
- **WHEN** the user submits credentials on the login page inside the desktop shell
- **THEN** authentication is performed against the local engine auth API
- **AND** the client does not require a remote official AChat business API host for login.

### Requirement: Frontend SHALL treat loopback host aliases as one engine service

In desktop mode, the frontend MUST treat `localhost`, `127.0.0.1`, and IPv6 loopback (`::1`) with the **same port** as the same local engine service when resolving `engineBaseUrl`, deciding whether a request targets the engine, and attaching the session engine token. The browser Origin model compares hostnames as strings (`http://localhost` ≠ `http://127.0.0.1`); the product MUST NOT assume same-machine implies same-origin, and MUST NOT half-align URLs (rewrite host in one layer while matching tokens with an unaligned string in another).

#### Scenario: Dev UI on localhost, engine on 127.0.0.1
- **WHEN** the page origin is `http://localhost:3000` (or similar)
- **AND** `window.achatDesktop.engineBaseUrl` is `http://127.0.0.1:<port>`
- **THEN** business REST calls to the local engine still include the injected engine token (for example `X-Engine-Token`)
- **AND** URL matching does not fail solely because the request host string is `localhost` while the bridge reports `127.0.0.1`.

#### Scenario: Half-alignment is forbidden
- **WHEN** any layer rewrites the engine base host to match the page hostname
- **THEN** token attachment and “is this the local engine?” checks MUST use the same loopback-aware logic
- **AND** a protected engine API MUST NOT return 401 solely due to a missing engine token caused by host-string mismatch.

### Requirement: Frontend SHALL surface local engine status

In desktop mode, the UI MUST expose enough status to understand whether the local engine is starting, ready, or failed, using `getEngineStatus()` and/or local health checks.

#### Scenario: Engine not ready
- **WHEN** desktop mode is active and the engine is not ready
- **THEN** the UI shows a starting or error state for local capabilities
- **AND** does not pretend local Agent execution is available.

### Requirement: Frontend SHALL use native directory picker in desktop mode for local bind flows

When the product flow binds a local filesystem directory and desktop mode is active, the frontend MUST prefer `window.achatDesktop.selectDirectory()` over web-only directory listing hacks that cannot access arbitrary user disks.

#### Scenario: Bind local workspace on desktop
- **WHEN** the user chooses to bind a local folder in desktop mode
- **THEN** the native picker is invoked
- **AND** the selected path is submitted to the local engine workspace binding flow.

### Requirement: Bridge types SHALL be shared and documented

The shape of `window.achatDesktop` MUST be declared in frontend TypeScript types (for example under `src/shared` or a desktop bridge module) so web and desktop builds share one contract.

#### Scenario: Typecheck in CI
- **WHEN** the frontend typechecks
- **THEN** references to `window.achatDesktop` are typed
- **AND** missing fields are caught at compile time for bridge consumers.
