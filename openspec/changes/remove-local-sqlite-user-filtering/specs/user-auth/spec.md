# Spec Delta: user-auth

## MODIFIED Requirements

### Requirement: All API endpoints SHALL require authentication

Every API router except `/api/auth/*` and `/health` MUST enforce authentication via the `get_current_user` FastAPI dependency. Unauthenticated requests MUST receive HTTP 401. Authentication verifies the user's identity (JWT) but does NOT imply data-level user isolation for local SQLite tables — local tables are single-user and do not filter by `user_id`. Remote (PostgreSQL) tables retain full `user_id`-based data isolation.

#### Scenario: Unauthenticated request to protected endpoint
- **WHEN** a GET `/api/conversations` arrives without a valid JWT
- **THEN** AChat returns HTTP 401.

#### Scenario: Authenticated request includes user context
- **WHEN** a valid JWT is present
- **THEN** the `get_current_user` dependency resolves the User object
- **AND** injects it into the request scope for downstream handlers
- **AND** local-table queries do NOT filter by `user_id` (single-user mode).

#### Scenario: Local-table ownership check is existence-only
- **WHEN** a request accesses a conversation or artifact by id
- **THEN** the ownership check verifies the row exists (404 if not found)
- **AND** does NOT compare `user_id` (no 403 for ownership mismatch on local tables).

#### Scenario: Remote-table ownership check retains user_id comparison
- **WHEN** a request accesses a document by id
- **THEN** the ownership check verifies the row exists AND `user_id` matches (404/403).
