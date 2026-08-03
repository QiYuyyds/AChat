# User Auth

## MODIFIED Requirements

### Requirement: JWT access tokens SHALL be long-lived

Access tokens MUST have an expiry of 10 years (315360000 seconds), making them effectively non-expiring for practical use. The frontend `authFetch` wrapper MUST still handle 401 responses by attempting a token refresh as a fallback, but under normal operation no refresh should be needed.

#### Scenario: Access token used throughout a session

- **WHEN** the frontend makes API calls with a valid access token
- **THEN** the server accepts the token without expiry rejection
- **AND** no silent refresh is triggered.

#### Scenario: Access token rejected after backend restart

- **WHEN** the backend restarts and regenerates the JWT secret (when `JWT_SECRET` is not explicitly set)
- **THEN** all previously issued access tokens fail verification
- **AND** `/api/auth/me` returns 401
- **AND** the frontend renders the guided state with the LoginDialog.

### Requirement: Refresh tokens SHALL be long-lived

AChat MUST provide a `/api/auth/refresh` endpoint that accepts a valid refresh token and returns a new access token. Refresh tokens MUST have an expiry of 10 years (315360000 seconds), making them effectively non-expiring. Session invalidation occurs via backend restart (JWT secret regeneration) or manual logout — not via token expiry.

#### Scenario: Refresh token is valid

- **WHEN** a POST `/api/auth/refresh` arrives with a non-expired refresh token
- **THEN** AChat returns a new access token.

#### Scenario: Refresh token invalid after backend restart

- **WHEN** the backend restarts and regenerates the JWT secret
- **THEN** previously issued refresh tokens fail verification
- **AND** `/api/auth/refresh` returns HTTP 401
- **AND** the frontend opens the LoginDialog (no route redirect).

### Requirement: Session invalidation SHALL occur on backend restart or manual logout

When `JWT_SECRET` is not explicitly configured in the environment, AChat MUST generate a new random JWT secret on each startup, invalidating all previously issued tokens. When `JWT_SECRET` IS explicitly set, tokens persist across restarts. Manual logout (`/api/auth/logout`) MUST clear the auth cookie and client-side token. Password changes and logout-all MUST increment `token_version`, invalidating all tokens for that user.

#### Scenario: Backend restarts without explicit JWT_SECRET

- **WHEN** the backend process restarts and `JWT_SECRET` is not set in the environment
- **THEN** a new JWT secret is generated
- **AND** all previously issued access and refresh tokens are invalid
- **AND** users must re-authenticate via the LoginDialog.

#### Scenario: Backend restarts with explicit JWT_SECRET

- **WHEN** the backend process restarts and `JWT_SECRET` is set in the environment
- **THEN** the JWT secret persists
- **AND** previously issued tokens remain valid
- **AND** users remain logged in without re-authentication.

#### Scenario: User manually logs out

- **WHEN** the user clicks "退出登录" in the sidebar dropdown
- **THEN** AuthStore calls `/api/auth/logout`, clearing the HttpOnly cookie
- **AND** clears client-side auth state (`isAuthenticated=false`, `user=null`)
- **AND** the workspace renders in guided state with the LoginDialog available.
