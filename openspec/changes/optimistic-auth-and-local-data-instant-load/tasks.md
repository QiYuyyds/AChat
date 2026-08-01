## 1. Backend — In-Process User Cache for `get_current_user`

- [x] 1.1 Add `_user_cache` module-level dict and `_USER_CACHE_TTL = 60` constant in `backend/app/auth/dependencies.py`
- [x] 1.2 Add per-user `asyncio.Lock` dict (`_user_locks`) to prevent thundering-herd PG queries on concurrent cache miss
- [x] 1.3 Modify `get_current_user`: after `verify_token()` succeeds, check `_user_cache[user_id]` — if present and not expired, compare `token_version` with JWT `ver`; mismatch → evict + raise 401; match → return cached User (no PG hit)
- [x] 1.4 Modify `get_current_user`: on cache miss/expired, acquire per-user lock, query PG, store `(User, time.time() + TTL)` in cache, return User
- [x] 1.5 Add `_invalidate_user_cache(user_id)` helper function for manual eviction (to be called by change-password / logout-all endpoints in future)
- [x] 1.6 Add a debug log line on cache hit/miss for observability (e.g., `logger.debug("user_cache %s", "hit" if cached else "miss")`)
- [x] 1.7 Run `ruff check .` on modified files

## 2. Frontend — AuthStore Optimistic Initialization

- [x] 2.1 Add `agenthub_auth_cache` localStorage key to persist `user` + `config` in `src/stores/auth-store.ts`
- [x] 2.2 Add `_loadCachedAuth()` helper: synchronously read `agenthub_auth_cache` from localStorage and return `{ user, config } | null`
- [x] 2.3 Modify `initialize()`: if `localStorage` has `agenthub_access_token`, immediately set `isAuthenticated=true`, `isLoading=false` using cached `user`/`config` (or defaults if cache missing). Fire `/api/auth/me` in the background; on success update `user` + `config` + persist to `agenthub_auth_cache`. On failure, let `authFetch` 401 handler deal with it.
- [x] 2.4 Modify `initialize()`: if no token in localStorage, keep existing behavior (call `/api/auth/config`, set `isLoading=false` after)
- [x] 2.5 Modify `login()` / `vipLogin()` / `register()`: after setting `user` + `config`, persist them to `agenthub_auth_cache` in localStorage
- [x] 2.6 Modify `logout()`: clear `agenthub_auth_cache` from localStorage alongside clearing token

## 3. Frontend — AuthGate Token-Presence Gate

- [x] 3.1 Add a synchronous `hasToken()` helper in `auth-store.ts` that checks `localStorage.getItem('agenthub_access_token')`
- [x] 3.2 Modify `AuthGate` (`src/components/auth-gate.tsx`): show `Loader2` spinner only when `isLoading && !hasToken()`. When token is present, render children immediately regardless of `isLoading`.
- [x] 3.3 Verify `useEffect` still calls `initialize()` once (guard via `useRef` unchanged)

## 4. Frontend — Sidebar Data-Fetch Guard

- [x] 4.1 In `src/components/sidebar.tsx`, import `hasToken` from auth-store (or read localStorage directly)
- [x] 4.2 Change the data-fetch `useEffect` guard from `if (!isAuthenticated) return` to `if (!hasToken()) return`
- [x] 4.3 Remove `isAuthenticated` from the `useEffect` dependency array; replace with a stable reference (the guard is synchronous, so no reactive dependency needed)
- [x] 4.4 Verify `useGuideSideEffectRefresh` callbacks (agents/conversations refresh) still work — they should fire when `guide_side_effect` SSE events arrive, independent of auth state

## 5. Verification & Testing

- [ ] 5.1 Manual test: returning user (token present) opens app — verify no loading spinner, conversations/agents appear within < 100ms
- [ ] 5.2 Manual test: first-time user (no token) opens app — verify loading spinner shows briefly, then WelcomeScreen renders
- [ ] 5.3 Manual test: backend restart (JWT secret regenerates) — verify optimistic render flashes briefly, then LoginDialog opens from `auth-expired` event
- [ ] 5.4 Manual test: change password while logged in — verify old token rejected within 60s (cache TTL), LoginDialog opens
- [ ] 5.5 Run `pnpm typecheck` and `pnpm lint` on modified frontend files
- [ ] 5.6 Run `pytest` on any affected backend tests (auth dependencies)
- [ ] 5.7 Verify no `console.log` / `print()` / `TODO` left in modified files
