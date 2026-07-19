## ADDED Requirements

### Requirement: Frontend SHALL detect desktop mode only via shell injection

The official frontend MUST treat the session as desktop mode when `window.achatDesktop?.isDesktop === true`. Absence of the injection MUST keep pure web behavior. The frontend MUST NOT permanently switch to desktop mode solely because an arbitrary localhost port responds.

#### Scenario: Browser visit without injection
- **WHEN** a user opens the official frontend in a normal browser without Tauri injection
- **THEN** desktop-only bridge APIs are not required
- **AND** the app behaves as the existing web client.

#### Scenario: Desktop webview with injection
- **WHEN** the page runs inside the desktop shell with `window.achatDesktop` injected
- **THEN** the frontend enables desktop engine integration paths.

### Requirement: Frontend SHALL call local engine with engine token

In desktop mode, requests to the local engine base URL MUST include the session engine token using the agreed header (for example `X-Engine-Token`). The frontend MUST obtain the token from `window.achatDesktop`, not from user-typed input.

#### Scenario: Desktop starts an agent run on the local engine
- **WHEN** the desktop frontend invokes a local engine API
- **THEN** the request targets `engineBaseUrl`
- **AND** includes the injected engine token.

### Requirement: Frontend SHALL continue using official cloud auth for account APIs

Login, registration, token refresh, and cloud-authoritative resource APIs MUST continue to use the existing user auth mechanisms against the official API. Engine token MUST NOT replace user JWT/cookies for cloud authorization.

#### Scenario: User logs in from desktop window
- **WHEN** the user submits credentials on the official login page inside the desktop shell
- **THEN** authentication is performed against the official cloud auth API
- **AND** subsequent cloud calls use the established user session.

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
