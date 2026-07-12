# Mobile Companion Delta: JWT Authentication

## MODIFIED Requirements

### Requirement: Mobile companion SHALL connect with user authentication

The mobile app MUST authenticate via `/api/auth/login` to obtain a JWT. The JWT MUST be included as `Authorization: Bearer <token>` on all mobile API requests. The previous environment-variable pairing token (`AGENTHUB_MOBILE_TOKEN`) SHALL be deprecated.

#### Scenario: Mobile client logs in
- **WHEN** the mobile app submits email and password to `/api/auth/login`
- **THEN** it receives a JWT and user profile
- **AND** stores the token securely for subsequent requests.

#### Scenario: Mobile client accesses snapshot
- **WHEN** the app requests `/api/mobile/snapshot` with a valid Bearer token
- **THEN** it receives only conversations and data belonging to the authenticated user.

## ADDED Requirements

### Requirement: Mobile APIs SHALL use the same auth dependency as desktop

All mobile companion API routes MUST use the `get_current_user` FastAPI dependency, identical to desktop routes. Mobile-specific CORS and response shaping SHALL remain, but the auth gate MUST be unified.

#### Scenario: Mobile and desktop share auth
- **WHEN** a mobile client and desktop client both authenticate as the same user
- **THEN** they see the same conversations, agents, and data.

### Requirement: Mobile token migration SHALL be supported

During the transition period, AChat MAY accept both the legacy `AGENTHUB_MOBILE_TOKEN` bearer token and user JWTs on mobile endpoints. After migration is complete, only JWTs SHALL be accepted.

#### Scenario: Legacy mobile token is used during migration
- **WHEN** a mobile request arrives with the legacy pairing token
- **THEN** AChat accepts it (if `AGENTHUB_MOBILE_TOKEN` is set) but logs a deprecation warning.

#### Scenario: Legacy mobile token is rejected after migration
- **WHEN** `AGENTHUB_MOBILE_TOKEN` is not set in the environment
- **THEN** only valid user JWTs are accepted on mobile endpoints.
