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

### Requirement: Frontend SHALL support dual-active-conversation model

AppState SHALL include `activeConversationId` (work conversation, unchanged) and `guideConversationId` (guide conversation, new). Both can be active simultaneously. SSE event reducers SHALL apply events to the correct conversation by `conversationId` bucketing. The guide floating panel SHALL read `guideConversationId` and SHALL NOT modify `activeConversationId`.

#### Scenario: User has both work and guide conversations active
- **WHEN** the user is in a work conversation and the guide panel is open
- **THEN** `activeConversationId` points to the work conversation
- **AND** `guideConversationId` points to the guide conversation
- **AND** SSE events for each conversation are applied to the correct message list.

#### Scenario: Guide conversation is auto-created on first login
- **WHEN** the user logs in and `guideConversationId` is null
- **THEN** the frontend creates a guide conversation (`mode='guide'`, `agentIds=['ag_guide_builtin']`)
- **AND** stores its id in `guideConversationId`
- **AND** opens the floating panel.

### Requirement: GuideFloatingPanel SHALL be a persistent floating component

The frontend SHALL render a `GuideFloatingPanel` component that floats above the main chat panel. The panel SHALL support drag (by header), resize (by corner handle), collapse/expand (by close button or `Ctrl/Cmd+G`), and position/size/open-state persistence to `localStorage` (per-user). The panel SHALL render a simplified message list (text/tool_use/ask_user parts only, no artifacts) and a simplified input (no attachments, no slash commands, no @mention). The panel's `z-index` SHALL be above the work chat panel but below modals/dialogs.

#### Scenario: Panel is dragged and resized
- **WHEN** the user drags the panel header to a new position and resizes via the corner handle
- **THEN** the new position and size are saved to `localStorage`
- **AND** restored on next page load.

#### Scenario: Panel is collapsed and expanded
- **WHEN** the user clicks the close button or presses `Ctrl/Cmd+G`
- **THEN** the panel collapses to a floating button (with unread indicator)
- **AND** clicking the floating button or pressing `Ctrl/Cmd+G` again expands it.

#### Scenario: ask_user renders inline in the panel
- **WHEN** the guide agent calls `ask_user`
- **THEN** the pending question renders inline in the panel's message list with option buttons
- **AND** the user's selection is sent via `POST /api/pending/questions/{id}/resolve`
- **AND** the option buttons are disabled after selection.

### Requirement: Frontend SHALL handle guide_side_effect events

The frontend SSE reducer SHALL handle `guide_side_effect` events by refreshing the corresponding panel. The `target` field determines which panel to refresh: `agents` → re-fetch agents list, `skills` → re-fetch skills, `mcp` → re-fetch MCP servers, `documents` → re-fetch documents, `memory` → re-fetch memories, `profile` → re-fetch profile/settings, `conversations` → re-fetch conversations list.

#### Scenario: guide_side_effect with target=agents
- **WHEN** the frontend receives a `guide_side_effect` event with `target='agents'`
- **THEN** it calls `fetchAgents()` to refresh the agents list
- **AND** the sidebar updates to show the new/updated/deleted agent.

#### Scenario: guide_side_effect with target=memory
- **WHEN** the frontend receives a `guide_side_effect` event with `target='memory'`
- **THEN** it refreshes the memory panel data
- **AND** the memory panel updates to reflect deletions/merges/updates.
