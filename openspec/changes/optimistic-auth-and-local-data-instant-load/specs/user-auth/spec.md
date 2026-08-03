# User Auth

## MODIFIED Requirements

### Requirement: All API endpoints SHALL require authentication

Every API router except `/api/auth/*` and `/health` MUST enforce authentication via the `get_current_user` FastAPI dependency. Unauthenticated requests MUST receive HTTP 401.

The `get_current_user` dependency MUST verify the JWT signature locally (via `verify_token`, sub-millisecond) on every request. The dependency MUST maintain an in-process cache mapping `user_id` to `(User, expires_at)` with a 60-second TTL. On cache hit (entry present and not expired), the dependency MUST skip the PostgreSQL `User` lookup and return the cached `User` object. On cache miss or TTL expiry, the dependency MUST query PostgreSQL, store the result in cache, and return it.

The dependency MUST compare the JWT's `ver` claim against the cached User's `token_version`. If they mismatch, the cache entry MUST be evicted and the dependency MUST raise 401 (credentials exception). This ensures that password changes and logout-all (`token_version` increment) propagate within at most 60 seconds.

Concurrent cache-miss requests for the same `user_id` MUST be serialized via an `asyncio.Lock` to prevent thundering-herd PG queries.

#### Scenario: Unauthenticated request to protected endpoint

- **WHEN** a GET `/api/conversations` arrives without a valid JWT
- **THEN** AChat returns HTTP 401.

#### Scenario: Authenticated request with warm cache

- **WHEN** a valid JWT is present and the user is in the in-process cache (not expired)
- **THEN** `get_current_user` verifies the JWT signature locally
- **AND** compares the JWT's `ver` claim against the cached `token_version`
- **AND** returns the cached User without querying PostgreSQL
- **AND** the request handler proceeds with local SQLite data access only (no PG round-trip).

#### Scenario: Authenticated request with cold cache (first request after TTL expiry)

- **WHEN** a valid JWT is present but the cache entry is expired or missing
- **THEN** `get_current_user` queries PostgreSQL for the User record
- **AND** stores the result in the in-process cache with a 60-second TTL
- **AND** returns the User.

#### Scenario: token_version mismatch (password changed)

- **WHEN** a valid JWT is present but the `ver` claim does not match the cached User's `token_version`
- **THEN** the cache entry is evicted
- **AND** `get_current_user` raises 401 (credentials exception)
- **AND** the frontend `authFetch` 401 handler triggers the LoginDialog.

#### Scenario: Concurrent requests with cold cache

- **WHEN** multiple concurrent API requests arrive for the same `user_id` and the cache is cold
- **THEN** only the first request queries PostgreSQL (acquires the per-user `asyncio.Lock`)
- **AND** subsequent requests wait for the lock, then read from the now-warm cache
- **AND** no more than one PG query is made per `user_id` per cache-miss window.
