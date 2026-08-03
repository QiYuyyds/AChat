# Frontend

## Purpose

Defines client state, event application, and major UI boundaries. Detailed frontend notes live in `specs/09-frontend-architecture.md`.

## Requirements

### Requirement: AuthStore SHALL manage authentication state

A Zustand `AuthStore` MUST track `user`, `isLoading`, `isAuthenticated`, and `showLoginDialog`, and provide `login()`, `vipLogin()`, `register()`, `logout()`, `refreshToken()`, `initialize()`, `openLoginDialog()`, and `closeLoginDialog()` actions. The store MUST persist the token across page reloads. The store MUST NOT perform route redirects — unauthenticated state is handled by conditional rendering in `page.tsx` and the `LoginDialog` component.

#### Scenario: App initializes with valid session

- **WHEN** the app first loads
- **THEN** AuthStore calls `/api/auth/me` to verify the existing cookie/token
- **AND** if valid, sets `user`, `isAuthenticated=true`, and renders the main workspace
- **AND** if invalid, sets `isAuthenticated=false` and renders the workspace in guided state (WelcomeScreen).

#### Scenario: App initializes after backend restart

- **WHEN** the app loads after a backend restart that regenerated the JWT secret
- **THEN** `/api/auth/me` returns 401
- **AND** AuthStore sets `isAuthenticated=false`, `user=null`
- **AND** the workspace renders in guided state with the WelcomeScreen and LoginDialog available.

#### Scenario: User logs out

- **WHEN** the user clicks logout
- **THEN** AuthStore calls `/api/auth/logout`
- **AND** clears user state and `isAuthenticated`
- **AND** the workspace renders in guided state (no route redirect).

#### Scenario: User opens login dialog

- **WHEN** an unauthenticated user clicks the "登录" button in the sidebar bottom bar or the WelcomeScreen call-to-action
- **THEN** AuthStore sets `showLoginDialog=true`
- **AND** the LoginDialog renders.

#### Scenario: User logs in via dialog

- **WHEN** the user submits credentials in the LoginDialog and login succeeds
- **THEN** AuthStore sets `user`, `isAuthenticated=true`, `showLoginDialog=false`
- **AND** the workspace transitions from guided state to the normal authenticated state
- **AND** StreamProvider establishes the SSE connection.

#### Scenario: Session expires mid-activity

- **WHEN** an API call returns 401 and token refresh also fails
- **THEN** AuthStore sets `isAuthenticated=false` and `showLoginDialog=true`
- **AND** the LoginDialog renders over the existing workspace (existing UI state is preserved).
- **AND** no route redirect occurs.

### Requirement: API client SHALL include authentication on every request

The frontend API client (`src/lib/api.ts`) MUST include the JWT on every fetch call. For same-origin requests, cookies are sent automatically. For cross-origin requests, an `authFetch` wrapper MUST inject the `Authorization: Bearer` header from a JS-accessible token mirror.

#### Scenario: Authenticated API call

- **WHEN** the frontend calls any `/api/*` endpoint
- **THEN** the request includes valid authentication
- **AND** the server accepts the request.

#### Scenario: Token refresh fails mid-session

- **WHEN** an API call returns 401
- **THEN** the frontend attempts a token refresh via `/api/auth/refresh`
- **AND** retries the original request once
- **AND** if refresh also fails, dispatches a `CustomEvent('auth-expired')` on `window`
- **AND** the AuthStore listener opens the LoginDialog (no route redirect).

### Requirement: Route guard SHALL render guided state for unauthenticated users

The frontend MUST render the main workspace for all users. When unauthenticated, `page.tsx` MUST render a `WelcomeScreen` component (with AChat branding and a login call-to-action) in place of the ChatPanel, and the Sidebar bottom bar MUST show a "登录" button instead of the avatar + settings dropdown. The `GuideFloatingPanel` MUST be hidden when unauthenticated. No route redirect to `/login` or `/register` SHALL occur.

#### Scenario: Unauthenticated user visits root

- **WHEN** an unauthenticated user visits `/`
- **THEN** the workspace renders with Sidebar (navigation disabled), WelcomeScreen, and hidden GuideFloatingPanel
- **AND** no redirect occurs.

#### Scenario: Unauthenticated user interacts with navigation

- **WHEN** an unauthenticated user clicks sidebar navigation buttons
- **THEN** the buttons are visually disabled or trigger the LoginDialog
- **AND** no API calls are made (no data fetching until authenticated).

#### Scenario: Authenticated user in normal workspace

- **WHEN** an authenticated user is in the workspace
- **THEN** the Sidebar shows the avatar + settings dropdown in the bottom bar
- **AND** the ChatPanel renders normally
- **AND** the GuideFloatingPanel is visible.

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
