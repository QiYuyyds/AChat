# Frontend

## MODIFIED Requirements

### Requirement: AuthStore SHALL manage authentication state

A Zustand `AuthStore` MUST track `user`, `isLoading`, `isAuthenticated`, and `showLoginDialog`, and provide `login()`, `vipLogin()`, `register()`, `logout()`, `refreshToken()`, `initialize()`, `openLoginDialog()`, and `closeLoginDialog()` actions. The store MUST persist the token across page reloads. The store MUST NOT perform route redirects — unauthenticated state is handled by conditional rendering in `page.tsx` and the `LoginDialog` component.

The `initialize()` action MUST use optimistic authentication: if a token exists in `localStorage` (`agenthub_access_token`), the store MUST immediately set `isAuthenticated=true` and `isLoading=false` without waiting for `/api/auth/me` to return. The store MUST persist `user` (id, email, name, avatarUrl) and `config` (allowRegistration, vipLoginEnabled) to `localStorage` under `agenthub_auth_cache` so that first render has user context. The `/api/auth/me` call MUST run in the background after optimistic render; if it succeeds, `user` and `config` MUST be updated from the response. If it fails (401), the existing `authFetch` 401 → `auth-expired` event → `LoginDialog` flow MUST handle it.

`AuthGate` MUST show the loading spinner (`isLoading` state) only when no token exists in `localStorage`. When a token is present, the workspace MUST render immediately regardless of `isLoading` state — the background `/api/auth/me` call MUST NOT block rendering.

#### Scenario: Returning user opens the app

- **WHEN** the app loads and `localStorage` contains `agenthub_access_token`
- **THEN** AuthStore sets `isAuthenticated=true`, `isLoading=false` immediately
- **AND** the workspace renders without a loading spinner
- **AND** `/api/auth/me` runs in the background and updates `user`/`config` if they've changed

#### Scenario: First-time user opens the app

- **WHEN** the app loads and `localStorage` has no `agenthub_access_token`
- **THEN** AuthStore sets `isLoading=true` and calls `/api/auth/config`
- **AND** `AuthGate` shows the loading spinner
- **AND** after config loads, `isLoading=false` and the workspace renders in guided state (WelcomeScreen)

#### Scenario: Returning user with invalid token (backend restart)

- **WHEN** the app loads with a token in `localStorage` but the backend has regenerated the JWT secret
- **THEN** the workspace renders optimistically (isAuthenticated=true)
- **AND** the first API call returns 401
- **AND** `authFetch` attempts token refresh; if refresh also fails, `auth-expired` event fires
- **AND** AuthStore sets `isAuthenticated=false`, `showLoginDialog=true`
- **AND** the LoginDialog renders over the workspace

#### Scenario: User logs out

- **WHEN** the user clicks logout
- **THEN** AuthStore calls `/api/auth/logout`, clearing the HttpOnly cookie
- **AND** clears client-side auth state (`isAuthenticated=false`, `user=null`)
- **AND** clears `agenthub_auth_cache` from `localStorage`
- **AND** the workspace renders in guided state (no route redirect).

#### Scenario: User opens login dialog

- **WHEN** an unauthenticated user clicks the "登录" button in the sidebar bottom bar or the WelcomeScreen call-to-action
- **THEN** AuthStore sets `showLoginDialog=true`
- **AND** the LoginDialog renders.

#### Scenario: User logs in via dialog

- **WHEN** the user submits credentials in the LoginDialog and login succeeds
- **THEN** AuthStore sets `user`, `isAuthenticated=true`, `showLoginDialog=false`
- **AND** persists `user` and `config` to `localStorage` (`agenthub_auth_cache`)
- **AND** the workspace transitions from guided state to the normal authenticated state
- **AND** StreamProvider establishes the SSE connection.

#### Scenario: Session expires mid-activity

- **WHEN** an API call returns 401 and token refresh also fails
- **THEN** AuthStore sets `isAuthenticated=false` and `showLoginDialog=true`
- **AND** the LoginDialog renders over the existing workspace (existing UI state is preserved).
- **AND** no route redirect occurs.

### Requirement: Route guard SHALL render guided state for unauthenticated users

The frontend MUST render the main workspace for all users. When unauthenticated, `page.tsx` MUST render a `WelcomeScreen` component (with AChat branding and a login call-to-action) in place of the ChatPanel, and the Sidebar bottom bar MUST show a "登录" button instead of the avatar + settings dropdown. The `GuideFloatingPanel` MUST be hidden when unauthenticated. No route redirect to `/login` or `/register` SHALL occur.

When a token exists in `localStorage` (optimistic auth), the Sidebar's data-fetch `useEffect` MUST fire immediately without waiting for auth verification to complete. The `useEffect` guard MUST check for token presence (`localStorage.getItem('agenthub_access_token')`), not `isAuthenticated` state.

#### Scenario: Unauthenticated user visits root

- **WHEN** an unauthenticated user visits `/`
- **THEN** the workspace renders with Sidebar (navigation disabled), WelcomeScreen, and hidden GuideFloatingPanel
- **AND** no redirect occurs.

#### Scenario: Unauthenticated user interacts with navigation

- **WHEN** an unauthenticated user clicks sidebar navigation buttons
- **THEN** the buttons are visually disabled or trigger the LoginDialog
- **AND** no API calls are made (no data fetching until a token is present).

#### Scenario: Authenticated user in normal workspace

- **WHEN** an authenticated user is in the workspace
- **THEN** the Sidebar shows the avatar + settings dropdown in the bottom bar
- **AND** the ChatPanel renders normally
- **AND** the GuideFloatingPanel is visible.

#### Scenario: Returning user's Sidebar fetches data immediately

- **WHEN** a returning user (token in localStorage) opens the app
- **THEN** the Sidebar's `useEffect` fires `fetchConversations()` and `fetchAgents()` on first render
- **AND** does not wait for the background `/api/auth/me` call to complete
- **AND** local SQLite data appears within the HTTP round-trip latency (sub-50ms on localhost)
