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

### Requirement: JWT access tokens SHALL be short-lived

Access tokens MUST expire within 1 hour of issuance. The frontend MUST silently refresh expired access tokens using the refresh token before making API calls.

#### Scenario: Access token expires during a session
- **WHEN** the frontend detects a 401 response from any API call
- **THEN** it calls `/api/auth/refresh` with the refresh token
- **AND** retries the original request with the new access token.

### Requirement: Refresh tokens SHALL enable session continuity

AChat MUST provide a `/api/auth/refresh` endpoint that accepts a valid refresh token and returns a new access token. Refresh tokens MUST expire within 7 days of issuance.

#### Scenario: Refresh token is valid
- **WHEN** a POST `/api/auth/refresh` arrives with a non-expired refresh token
- **THEN** AChat returns a new access token.

#### Scenario: Refresh token is expired
- **WHEN** the refresh token has passed its expiry
- **THEN** AChat returns HTTP 401
- **AND** the frontend redirects to the login page.

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
