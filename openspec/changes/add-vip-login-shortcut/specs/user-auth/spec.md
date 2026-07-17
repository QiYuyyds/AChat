# User Auth

## ADDED Requirements

### Requirement: Default account SHALL support optional VIP shortcut login

AChat MUST provide `POST /api/auth/vip-login` when `VIP_LOGIN_ENABLED=true`. The endpoint MUST accept a password, resolve the existing user identified by `DEFAULT_USER_EMAIL`, verify the stored bcrypt hash, and issue the same JWT pair and HttpOnly cookie as the standard login endpoint. This shortcut MUST NOT create a role, permission level, user type, or database field.

#### Scenario: VIP login succeeds
- **WHEN** VIP login is enabled and the submitted password matches the configured default user's stored password hash
- **THEN** AChat returns the existing default user's profile and JWT pair
- **AND** sets the same HttpOnly auth cookie as standard login
- **AND** the authenticated user has exactly the same permissions as a normal authenticated user.

#### Scenario: VIP password is invalid
- **WHEN** the submitted password does not match the stored hash
- **THEN** AChat returns HTTP 401 with a generic invalid-credentials message
- **AND** does not reveal the configured default email.

#### Scenario: VIP login is disabled
- **WHEN** `VIP_LOGIN_ENABLED=false`
- **THEN** `POST /api/auth/vip-login` returns HTTP 404.

#### Scenario: Default user is missing
- **WHEN** VIP login is enabled but no user matches `DEFAULT_USER_EMAIL`
- **THEN** AChat returns a generic authentication failure
- **AND** does not reveal whether the account exists.

### Requirement: VIP account password SHALL be reset only from the server

AChat MUST provide a server-side reset script that reads `DEFAULT_USER_EMAIL` and `DEFAULT_USER_PASSWORD`, updates the matching user's bcrypt password hash, and increments `token_version`. AChat MUST NOT provide a VIP-specific password-change control in the frontend.

#### Scenario: Server owner resets the password
- **WHEN** the server owner updates `DEFAULT_USER_PASSWORD` and runs the reset script
- **THEN** the default user's password hash is replaced with a bcrypt hash of the configured password
- **AND** `token_version` is incremented
- **AND** all previously issued access and refresh tokens become invalid.

#### Scenario: Reset password is empty
- **WHEN** the reset script receives an empty `DEFAULT_USER_PASSWORD`
- **THEN** it exits without changing the stored password.

