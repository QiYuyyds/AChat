# User Auth

## MODIFIED Requirements

### Requirement: Users table SHALL store identity and credentials

AChat MUST persist a `users` table with columns: `id` (UUID PK), `email` (unique, NOT NULL), `name` (NOT NULL), `password_hash` (bcrypt, NOT NULL), `avatar_url` (nullable), `token_version` (integer, default 0), `created_at`, `updated_at`. The `avatar_url` column MAY be populated via the `POST /api/profile/avatar` endpoint and served via `GET /api/profile/avatar`.

#### Scenario: New user is created
- **WHEN** a registration succeeds
- **THEN** a row is inserted into `users` with a bcrypt-hashed password and `token_version=0`
- **AND** `avatar_url` is initially NULL.

#### Scenario: User uploads an avatar
- **WHEN** a user uploads an avatar via `POST /api/profile/avatar`
- **THEN** `users.avatar_url` is set to `/api/profile/avatar`
- **AND** `/api/auth/me` returns the updated `avatarUrl` in the user profile object.

#### Scenario: User has no avatar
- **WHEN** a user has never uploaded an avatar
- **THEN** `users.avatar_url` is NULL
- **AND** `/api/auth/me` returns `avatarUrl: null`.
