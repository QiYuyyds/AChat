# Frontend

## REMOVED Requirements

### Requirement: Frontend SHALL provide login and register pages

**Reason**: Standalone `/login` and `/register` pages are removed. Authentication is now performed via an in-app `LoginDialog` component triggered from the sidebar or welcome screen. Registration UI is removed entirely (the `/api/auth/register` backend endpoint is retained for admin/scripted use).

**Migration**: Users who bookmarked `/login` or `/register` will see a 404. The root path `/` now always renders the main workspace (in guided state when unauthenticated). Existing login form logic is extracted into `src/components/login-dialog.tsx`.

## MODIFIED Requirements

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
