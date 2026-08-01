# Optimistic Auth & Local Data Instant Load

## Why

When a user opens the app, all data — including conversations and agents stored in local SQLite — shows a visible loading spinner. This happens because (1) `AuthGate` blocks rendering until `/api/auth/me` returns from remote PostgreSQL, and (2) every API request — even for local SQLite data — runs `get_current_user` which queries PostgreSQL for user lookup and `token_version` verification. With 10-year non-expiring JWTs and a planned desktop mode where the backend runs locally, this PG round-trip on every request is unnecessary friction. Local data should appear instantly; only genuinely remote data should have a loading state.

## What Changes

### Frontend — Optimistic AuthGate

- **BREAKING**: `AuthStore.initialize()` no longer blocks with `isLoading=true` when a token exists in `localStorage`. Instead, it immediately sets `isAuthenticated=true` (optimistic) and renders the workspace. The `/api/auth/me` call runs in the background to verify; if it fails, `authFetch`'s existing 401 → `auth-expired` → `LoginDialog` flow handles it.
- `AuthGate` only shows the loading spinner when **no token exists** at all (first-time user / fresh install). Returning users see the workspace instantly.
- `Sidebar`'s `useEffect` removes the `if (!isAuthenticated) return` guard — conversations and agents are fetched immediately on mount when a token is present, without waiting for auth verification.
- `AuthStore` persists `user` profile (id, email, name, avatarUrl) and `config` to `localStorage` so the UI has user context on first render without waiting for `/api/auth/me`.

### Backend — In-Process User Cache for `get_current_user`

- `get_current_user` gains an in-process TTL cache (60s) mapping `user_id → (User, expires_at)`. On cache hit, the PG lookup is skipped entirely; JWT signature verification (`verify_token`) still runs (local crypto, sub-millisecond). On cache miss or TTL expiry, one PG query refreshes the cache.
- Cache is invalidated on `token_version` mismatch (the JWT's `ver` is compared against the cached User's `token_version`; mismatch → cache evict + 401).
- No new dependencies, no new tables, no schema changes. The cache is a process-local `dict` guarded by an `asyncio.Lock` for concurrent-refresh safety.

### Non-Goals

- Frontend Zustand `persist` middleware for store-level data caching (conversations/agents). This is a future optimization; the optimistic auth + backend cache already eliminates the perceived loading delay for local data.
- Electron IPC for direct SQLite access (desktop-only, future change).
- Splitting API endpoints into "local-verified" vs "remote-verified" tiers. The TTL cache approach is simpler and achieves the same latency goal for the common case.

## Capabilities

### New Capabilities

_(none — no new capability domains introduced)_

### Modified Capabilities

- `frontend`: AuthStore initialization changes from blocking (`isLoading` gate) to optimistic (token-presence gate). Sidebar data-fetch no longer waits for auth verification. AuthStore persists `user` + `config` to `localStorage` for first-render context.
- `user-auth`: `get_current_user` adds an in-process TTL cache (60s) to skip PG user-lookup on cache hit. JWT signature verification remains mandatory on every request. Cache eviction on `token_version` mismatch preserves security.

## Impact

- **Frontend modified code**: `src/stores/auth-store.ts` (initialize → optimistic, add localStorage persistence of user/config), `src/components/auth-gate.tsx` (isLoading only when no token), `src/components/sidebar.tsx` (remove `isAuthenticated` guard from data-fetch useEffect)
- **Backend modified code**: `backend/app/auth/dependencies.py` (add `_user_cache` dict + TTL logic + `asyncio.Lock` to `get_current_user`)
- **Backend unchanged**: `backend/app/auth/jwt_handler.py` (verify_token logic unchanged), `backend/app/api/` routes (no endpoint changes), `backend/app/db/` (no schema changes)
- **Specs**: `frontend` and `user-auth` specs updated to reflect optimistic auth flow and `get_current_user` caching behavior
