# Frontend

## Purpose

Defines client state, event application, and major UI boundaries. Detailed frontend notes live in `specs/09-frontend-architecture.md`.

## Requirements

### Requirement: Frontend SHALL provide login and register pages

AChat MUST provide `/login` and `/register` pages accessible without authentication. The login page MUST accept email and password. The register page MUST accept email, name, and password (>= 8 chars).

#### Scenario: User navigates to login
- **WHEN** an unauthenticated user visits any page
- **THEN** they are redirected to `/login`.

#### Scenario: User registers
- **WHEN** a user fills the register form and submits
- **THEN** the frontend calls `/api/auth/register`
- **AND** on success, stores the user in AuthStore and redirects to the main workspace.

### Requirement: AuthStore SHALL manage authentication state

A Zustand `AuthStore` MUST track `user`, `isLoading`, and provide `login()`, `register()`, `logout()`, and `initialize()` actions. The store MUST persist the token across page reloads.

#### Scenario: App initializes
- **WHEN** the app first loads
- **THEN** AuthStore calls `/api/auth/me` to verify the existing cookie/token
- **AND** if valid, sets `user` and renders the main workspace
- **AND** if invalid, redirects to `/login`.

#### Scenario: User logs out
- **WHEN** the user clicks logout
- **THEN** AuthStore calls `/api/auth/logout`
- **AND** clears user state
- **AND** redirects to `/login`.

### Requirement: API client SHALL include authentication on every request

The frontend API client (`src/lib/api.ts`) MUST include the JWT on every fetch call. For same-origin requests, cookies are sent automatically. For cross-origin requests, an `authFetch` wrapper MUST inject the `Authorization: Bearer` header from a JS-accessible token mirror.

#### Scenario: Authenticated API call
- **WHEN** the frontend calls any `/api/*` endpoint
- **THEN** the request includes valid authentication
- **AND** the server accepts the request.

#### Scenario: Token expires mid-session
- **WHEN** an API call returns 401
- **THEN** the frontend attempts a token refresh via `/api/auth/refresh`
- **AND** retries the original request once
- **AND** if refresh also fails, redirects to `/login`.

### Requirement: Route guard SHALL protect authenticated pages

The frontend MUST prevent unauthenticated access to the main workspace. A client-side guard in the root layout or page component MUST check `AuthStore.user` before rendering protected content.

#### Scenario: Unauthenticated user visits root
- **WHEN** an unauthenticated user visits `/`
- **THEN** the route guard redirects to `/login`
- **AND** the main workspace is not rendered.

#### Scenario: Authenticated user visits login page
- **WHEN** an already-authenticated user visits `/login`
- **THEN** they are redirected to `/`.

### Requirement: Frontend SHALL consume server APIs and SSE

The frontend MUST use REST routes and SSE stream events; it SHALL not import or call LLM SDKs directly. All API calls MUST include authentication. The SSE connection MUST be established only after authentication is confirmed.

#### Scenario: User sends a message
- **WHEN** the UI posts to the messages API with a valid JWT
- **THEN** server-side AgentRunner invokes the adapter
- **AND** UI updates arrive through SSE events filtered by user.

#### Scenario: SSE connects after login
- **WHEN** the user logs in successfully
- **THEN** the frontend establishes the SSE connection with the auth cookie
- **AND** events are received only for the authenticated user.

### Requirement: Store reducers SHALL apply StreamEvent deterministically

Zustand reducers MUST update conversation, message, artifact, pending write, pending bash command, dispatch, and usage state from `StreamEvent` payloads.

#### Scenario: `part.delta` arrives
- **WHEN** the event references an existing part
- **THEN** the store appends content to that part without reordering other parts.

#### Scenario: A failed run leaves an open tool call
- **WHEN** `run.end` arrives with `status='failed'` or `status='aborted'`
- **THEN** the store marks streaming messages from that run as terminal
- **AND** appends local error `tool_result` parts for any unmatched `tool_use` call ids.

### Requirement: Artifact preview SHALL be separate from chat rendering

The UI MUST render artifact previews in a dedicated panel and render chat artifact references as cards or links.

#### Scenario: User clicks artifact ref
- **WHEN** an `artifact_ref` part is selected
- **THEN** the preview panel opens the referenced artifact.

### Requirement: Preview URLs SHALL be one-click actions

For `web_app` artifacts and ready deployment status parts, the UI MUST provide open and copy actions for the preview URL. Deployment cards SHOULD distinguish local previews from externally published static deployments.

#### Scenario: Deployment card is ready
- **WHEN** a `deploy_status` part has `status='ready'`
- **THEN** the chat renders a deployment card with open and copy controls.

#### Scenario: Deployment card has external publish metadata
- **WHEN** a `deploy_status` part has `deploymentType='external_static'`
- **THEN** the card labels it as an external static publish
- **AND** shows the local preview fallback when available.

### Requirement: Agent builder SHALL expose adapter-specific fields

Create/edit agent UI MUST show provider, model, tool, key, and base URL fields according to selected adapter semantics. For Custom adapter, the tools tab MUST display a read-only baseline tools section (9 tools) and 5 UI-selectable tool checkboxes, plus 4 role preset buttons (coder / researcher / orchestrator / writer). For SDK adapters, tool checkboxes and baseline hints MUST be hidden.

#### Scenario: User selects Codex adapter
- **WHEN** `adapterKind='codex'`
- **THEN** provider and AChat tool checkboxes are hidden
- **AND** Base URL copy says it must support Codex/Responses.

#### Scenario: User selects Custom adapter
- **WHEN** `adapterKind='custom'`
- **THEN** the tools tab shows a read-only section listing 9 baseline tools
- **AND** 5 UI-selectable tool checkboxes are displayed below
- **AND** 4 role preset buttons (coder / researcher / orchestrator / writer) are available.
