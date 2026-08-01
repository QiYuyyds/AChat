# Design: Optimistic Auth & Local Data Instant Load

## Context

AChat uses a dual-DB architecture: local SQLite (10 tables: conversations, agents, messages, artifacts, etc.) and remote PostgreSQL (12 tables: users, user_settings, documents, RAG, memory, etc.). JWTs are 10-year non-expiring; once a user logs in, they stay logged in until the backend restarts (JWT secret regenerates) or they manually log out.

Despite local SQLite being fast (sub-millisecond queries), users see a visible loading spinner for all data on first open. Two root causes:

1. **Frontend blocking**: `AuthGate` blocks all rendering while `AuthStore.initialize()` calls `/api/auth/me` (which hits PG). Only after this returns does `Sidebar` mount and fetch conversations/agents from SQLite.
2. **Backend per-request PG hit**: Every API endpoint uses the `get_current_user` FastAPI dependency, which queries PG (`select(User).where(User.id == user_id)`) on every request — even for local SQLite data endpoints like `/api/conversations` and `/api/agents`. The PG lookup exists solely to verify `token_version` (password-change / logout-all detection).

The project is heading toward desktop mode (Electron + embedded Python, see `desktop-electron-python` change). In desktop mode, the backend runs at `127.0.0.1` and `JWT_SECRET` is persisted across restarts — making the PG round-trip even more incongruent with the "local app" feel.

## Goals

- Returning users see conversations and agents instantly on page open (< 50ms perceived)
- No loading spinner for local SQLite data — only remote PG data (documents, memory, RAG) shows a loading state
- `get_current_user` does not hit PG on every request — process-level cache skips PG for warm-cache requests
- Security is preserved: JWT signature verification runs on every request; `token_version` mismatch is detected within TTL window
- Backward compatible: existing web mode (remote FastAPI) and future desktop mode both benefit

## Non-Goals

- Zustand `persist` middleware for store-level caching of conversations/agents (future optimization)
- Electron IPC for direct SQLite access bypassing HTTP (desktop-only, future change)
- Splitting API endpoints into "local-verified" vs "remote-verified" tiers (considered, rejected — see Decisions)
- Changing JWT structure, token expiry, or auth cookie behavior
- Adding Redis or external cache (process-local dict is sufficient)

## Decisions

### D1: Optimistic AuthGate — token-presence gate, not auth-verification gate

**Choice**: If `localStorage` has `agenthub_access_token`, set `isAuthenticated=true` immediately and render the workspace. Run `/api/auth/me` in the background; if it fails, the existing `authFetch` 401 → `auth-expired` event → `LoginDialog` flow handles it.

**Rationale**: The JWT is 10-year non-expiring. The probability of an expired/invalid token on a returning user's page load is near-zero. Blocking the entire UI for a verification that almost always succeeds is a poor tradeoff.

**Alternatives considered**:
- *Keep blocking, but parallelize auth + data fetch*: Auth still blocks, but conversations/agents fetch starts in parallel. Problem: AuthGate still shows spinner, and local data fetch would hit 401 if the token is actually invalid (creating wasted requests).
- *Server Components pre-fetch*: Next.js RSC reads SQLite at build/request time. Problem: requires restructuring from pure client-side to RSC, too large a change for this goal.

### D2: Persist `user` + `config` to localStorage

**Choice**: AuthStore persists the `user` object (id, email, name, avatarUrl) and `config` (allowRegistration, vipLoginEnabled) to `localStorage` under `agenthub_auth_cache`. On optimistic render, these are read for immediate UI context. The background `/api/auth/me` call updates them if they've changed (e.g., avatar updated on another device).

**Rationale**: Without persisting user data, the optimistic render would show empty avatar/name in the sidebar until the background fetch completes. This is a 2-field localStorage write — trivially small.

**Alternatives considered**:
- *Decode JWT for user info*: The JWT already contains `sub` (user_id) and `email`. We could decode it client-side. Problem: JWT doesn't contain `name` or `avatarUrl`, so we'd still need a fetch for those. localStorage persistence is simpler and covers all fields.

### D3: In-process user cache with TTL (60s) in `get_current_user`

**Choice**: Add a module-level `dict[str, tuple[User, float]]` (`_user_cache`) in `dependencies.py`. On `get_current_user`:
1. `verify_token()` — local HMAC-SHA256, always runs (security: token signature + expiry check)
2. Extract `user_id` + `token_ver` from JWT payload
3. Check `_user_cache[user_id]` — if present and not expired (TTL 60s), compare cached `token_version` with JWT's `ver`. Mismatch → 401 (evict cache). Match → return cached User (no PG hit).
4. Cache miss / expired → query PG, store result in cache, return.

An `asyncio.Lock` per `user_id` prevents concurrent cache-miss requests from all hitting PG simultaneously (thundering herd).

**Rationale**: The only reason `get_current_user` hits PG is to check `token_version`. With 10-year JWTs, `token_version` changes only on password change or logout-all — extremely rare. A 60-second TTL means at most 1 PG query per minute per user, instead of 1 PG query per API request. For a typical page load that fires 3–5 API calls, this eliminates 2–4 PG round-trips.

**TTL = 60s rationale**: Long enough to cover a burst of API calls on page load (conversations + agents + messages + profile). Short enough that a `token_version` change (password change) propagates within 1 minute — acceptable for a local-run app where the user changing their password is the same person using the app.

**Alternatives considered**:
- *No cache, trust JWT's `ver` field only*: Skip PG entirely, trust the JWT's `ver` claim as authoritative. Problem: if user A changes password (incrementing `token_version` in PG), user A's old JWT still has the old `ver` and would be trusted until the 10-year expiry. The cache approach catches this within 60s.
- *Split into local-verified vs remote-verified endpoints*: Local data endpoints use JWT-only verification; remote endpoints do full PG lookup. Problem: requires per-route dependency annotation changes across all API routers, and the security boundary is unclear. The TTL cache achieves the same latency goal with a single change in one file.
- *Redis cache*: External dependency, adds operational complexity. Process-local dict is sufficient for a single-process FastAPI backend.

### D4: Sidebar removes `isAuthenticated` guard from data-fetch useEffect

**Choice**: The `useEffect` in `Sidebar.tsx` that fetches conversations and agents changes its guard from `if (!isAuthenticated) return` to `if (!hasToken) return`, where `hasToken` is a synchronous check of `localStorage.getItem('agenthub_access_token')`.

**Rationale**: With optimistic auth, `isAuthenticated` is `true` before the background verification completes. But we still need to gate on token presence — a first-time user with no token should not fire API calls (they'd all 401). The token-presence check is synchronous, so the `useEffect` fires on first render without delay.

### D5: AuthGate loading spinner only for zero-token state

**Choice**: `AuthGate` shows the `Loader2` spinner only when `isLoading && !hasToken` (no token in localStorage at all). When a token exists, `isLoading` is bypassed — the workspace renders immediately.

**Rationale**: First-time users (no token) still need the spinner because `initialize()` must call `/api/auth/config` to determine if registration/VIP login is available before showing the WelcomeScreen. Returning users (token present) skip straight to the workspace.

## Risks / Trade-offs

- **[Risk] Stale user data on optimistic render** — The persisted `user` in localStorage may be outdated (e.g., avatar changed on another device). → Mitigation: background `/api/auth/me` call updates `user` + `config` in AuthStore when it returns; components re-render with fresh data. The stale window is < 1 second (background fetch latency).

- **[Risk] `token_version` change delayed by up to 60s** — If a user changes their password, old tokens remain valid for local-data endpoints for up to 60 seconds (cache TTL). → Mitigation: Acceptable for a local-run app where the password-changer is the same person. For multi-user server deployments, the 60s window is a minor security tradeoff that can be revisited. The cache could also be manually invalidated by the change-password endpoint (call `_user_cache.pop(user_id)`).

- **[Risk] localStorage token exposure** — The access token is already stored in `localStorage` (existing behavior since `replace-login-page-with-dialog`). This change reads it earlier in the lifecycle. → Mitigation: No new exposure — the token was already in localStorage. XSS risk is unchanged.

- **[Risk] Race condition: optimistic render + 401** — If the token is invalid, the user briefly sees the workspace (with empty data) before `LoginDialog` opens. → Mitigation: `authFetch`'s 401 handler dispatches `auth-expired` → `LoginDialog` opens. The flash is < 200ms (one failed fetch round-trip). For local SQLite data, if `get_current_user` returns 401, the fetch fails silently (`.catch(console.error)`), and the LoginDialog opens from the `auth-expired` event.

- **[Trade-off] Cache memory** — `_user_cache` holds User objects in memory. With a single-process backend and a small number of active users, this is trivially small (a few KB per user).

## Migration Plan

1. **Backend first**: Add `_user_cache` + TTL to `get_current_user`. No behavioral change visible to frontend — just faster responses. Deploy and verify cache hit/miss via logs.
2. **Frontend second**: Change `AuthStore.initialize()` to optimistic mode. Change `AuthGate` to token-presence gate. Remove `isAuthenticated` guard from `Sidebar` useEffect. Add `localStorage` persistence of `user` + `config`.
3. **Rollback**: If issues arise, the backend cache change is self-contained (remove the cache dict + TTL check, revert to direct PG query). The frontend change is self-contained (revert `initialize()` to blocking mode, restore `AuthGate` spinner-on-`isLoading`). No database migration, no data migration, no breaking API changes.

## Open Questions

- Should the `change-password` and `logout-all` endpoints explicitly evict the user from `_user_cache`? (Currently relying on TTL. Explicit eviction is trivial but requires importing `_user_cache` into the auth service — circular dependency risk to assess during implementation.)
- Should the TTL be configurable via an environment variable? (Current proposal hardcodes 60s. If multi-user server deployments need different behavior, a `USER_CACHE_TTL_SECONDS` env var is a simple addition.)
