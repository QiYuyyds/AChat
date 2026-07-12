# Frontend Delta: Auth Store, Login Page, and Route Guard

## ADDED Requirements

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

## MODIFIED Requirements

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
