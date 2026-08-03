# User Auth

## Purpose

Defines user identity, registration, login, JWT lifecycle, and per-request authentication for AChat's multi-user remote access mode.

## Requirements

### Requirement: Users SHALL register with email and password

AChat MUST provide a `/api/auth/register` endpoint that accepts email, name, and password. The endpoint MUST validate email uniqueness, hash the password with bcrypt, create a User record, and return a JWT pair (access + refresh). Registration MAY be disabled via the `ALLOW_REGISTRATION` environment variable.

#### Scenario: New user registers successfully
- **WHEN** a POST `/api/auth/register` arrives with a valid email, name, and password (>= 8 chars)
- **THEN** AChat creates a User row with a bcrypt-hashed password
- **AND** returns `access_token`, `refresh_token`, and user profile in the response body
- **AND** sets the access token as an HttpOnly cookie.

#### Scenario: Duplicate email is rejected
- **WHEN** a registration request uses an email that already exists
- **THEN** AChat returns HTTP 409 with an error message.

#### Scenario: Registration is disabled
- **WHEN** `ALLOW_REGISTRATION=false` is set in the environment
- **THEN** the register endpoint returns HTTP 403.

### Requirement: Users SHALL log in with email and password

AChat MUST provide a `/api/auth/login` endpoint that verifies credentials and issues JWT tokens.

#### Scenario: Valid login
- **WHEN** a POST `/api/auth/login` arrives with correct email and password
- **THEN** AChat verifies the bcrypt hash against the stored password
- **AND** returns `access_token`, `refresh_token`, and user profile
- **AND** sets the access token as an HttpOnly cookie.

#### Scenario: Invalid credentials
- **WHEN** the password does not match the stored hash
- **THEN** AChat returns HTTP 401 with a generic "invalid credentials" message.

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

### Requirement: All API endpoints SHALL require authentication

Every API router except `/api/auth/*` and `/health` MUST enforce authentication via the `get_current_user` FastAPI dependency. Unauthenticated requests MUST receive HTTP 401.

#### Scenario: Unauthenticated request to protected endpoint
- **WHEN** a GET `/api/conversations` arrives without a valid JWT
- **THEN** AChat returns HTTP 401.

#### Scenario: Authenticated request includes user context
- **WHEN** a valid JWT is present
- **THEN** the `get_current_user` dependency resolves the User object
- **AND** injects it into the request scope for downstream handlers.

### Requirement: Passwords SHALL be stored as bcrypt hashes

AChat MUST hash passwords using bcrypt with a cost factor of at least 12. Plaintext passwords MUST NEVER be stored or logged.

#### Scenario: Password is set during registration
- **WHEN** a user submits a password
- **THEN** AChat hashes it with bcrypt before persisting
- **AND** the plaintext is discarded immediately.

### Requirement: Password changes SHALL invalidate existing tokens

AChat MUST store a `token_version` integer on the User record. JWTs MUST embed this version. When a user changes their password, AChat MUST increment `token_version`, invalidating all previously issued tokens.

#### Scenario: User changes password
- **WHEN** a user updates their password via `/api/auth/change-password`
- **THEN** `token_version` is incremented
- **AND** all existing access and refresh tokens become invalid.

#### Scenario: Logout from all devices
- **WHEN** a user calls `/api/auth/logout-all`
- **THEN** `token_version` is incremented
- **AND** the user must re-authenticate on all devices.

### Requirement: JWTs SHALL encode user identity and token version

Access tokens MUST include `sub` (user_id), `email`, `type: "access"`, `exp`, `iat`, and `ver` (token_version). Refresh tokens MUST include `sub`, `type: "refresh"`, `exp`, `iat`, and `ver`.

#### Scenario: JWT is verified
- **WHEN** the auth middleware decodes a JWT
- **THEN** it checks `exp` is in the future
- **AND** checks `ver` matches the user's current `token_version`
- **AND** checks `type` is `"access"` for API requests.

### Requirement: Logout SHALL clear the auth cookie

AChat MUST provide a `/api/auth/logout` endpoint that clears the HttpOnly cookie and returns HTTP 200. The frontend MUST also clear any client-side auth state.

#### Scenario: User logs out
- **WHEN** a POST `/api/auth/logout` arrives
- **THEN** AChat clears the HttpOnly cookie
- **AND** the frontend clears the AuthStore user state.

### Requirement: CSRF protection SHALL guard mutation endpoints

All POST, PATCH, and DELETE endpoints MUST verify that the `Origin` header matches an allowed origin when the request is cookie-authenticated.

#### Scenario: Cross-origin POST without matching Origin
- **WHEN** a POST request arrives with an `Origin` header not in `cors_origins_list`
- **THEN** AChat returns HTTP 403.
