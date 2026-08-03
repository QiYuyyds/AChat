# Design: Replace Login Page With In-App Login Dialog

## Context

AChat currently uses a standalone `/login` page (`src/app/login/page.tsx`) for authentication. `AuthGate` (`src/components/auth-gate.tsx`) redirects unauthenticated users to `/login` and redirects authenticated users away from public routes. JWT tokens are short-lived (1h access / 7d refresh), causing periodic re-login friction.

The existing `Sidebar` `BottomActionBar` (`src/components/sidebar.tsx:400`) already has a dual-purpose pattern: avatar + dropdown menu for authenticated users. The `authFetch` wrapper (`src/lib/api.ts:84`) handles 401 with auto-refresh but falls back to leaving the 401 response (no redirect logic in api.ts itself — the redirect is in AuthGate).

### Existing component contracts

- `AuthGate` (`src/components/auth-gate.tsx`): wraps `StreamProvider` in `layout.tsx`; calls `AuthStore.initialize()` on mount; `PUBLIC_ROUTES = ['/login', '/register']`; redirects via `router.replace()`.
- `AuthStore` (`src/stores/auth-store.ts`): Zustand store with `user`, `isAuthenticated`, `isLoading`, `config`, `login()`, `vipLogin()`, `register()`, `logout()`, `refreshToken()`, `initialize()`.
- `StreamProvider` (`src/components/stream-provider.tsx`): already guards on `if (!isAuthenticated) return` — no changes needed.
- `ChatPanel` (`src/components/chat-panel.tsx:150`): has an empty-state when no conversation is selected (`if (!conv)`) — shows a welcome message with profile name.
- `BottomActionBar` (`src/components/sidebar.tsx:400`): renders avatar + name + settings dropdown when authenticated.

## Goals / Non-Goals

**Goals:**
- Remove standalone `/login` and `/register` pages entirely
- Unauthenticated users land directly in the main workspace in a read-only "guided" state
- Login is triggered via a `LoginDialog` (Dialog component) opened from the sidebar bottom-left or the welcome screen
- JWT tokens effectively never expire (10-year expiry); session invalidates only on backend restart or manual logout
- 401 with failed refresh opens the login dialog instead of redirecting

**Non-Goals:**
- Changing the backend auth API surface (`/api/auth/*` endpoints remain unchanged)
- Supporting registration UI in the new flow (registration page removed; `/api/auth/register` API retained for admin/scripted use)
- Adding "remember me" or "stay logged in" toggle (the default behavior IS stay-logged-in)
- Changing multi-user isolation or RBAC
- Modifying the Guide Agent's backend behavior (only its frontend visibility when unauthenticated)

## Decisions

### D1: AuthGate — from gatekeeper to state probe

**Choice**: `AuthGate` always renders children. Remove `PUBLIC_ROUTES`, remove `router.replace()` calls. Only responsibility: call `initialize()` and show a loading spinner while `isLoading`.

**Rationale**: The gate's only job becomes "figure out auth state". What to do about it (show login dialog, lock UI, etc.) is downstream concern. This is simpler and more composable.

**Alternative considered**: Keep AuthGate as a conditional renderer (render children only if authenticated, else render WelcomeScreen). Rejected — WelcomeScreen needs to coexist with the Sidebar (for the login button in the bottom bar), so it must be rendered at the `page.tsx` level, not inside AuthGate.

### D2: WelcomeScreen — conditional rendering in page.tsx

**Choice**: `page.tsx` checks `isAuthenticated` from AuthStore. When unauthenticated, renders `<WelcomeScreen />` in place of `<ChatPanel />`. Sidebar is always rendered. `GuideFloatingPanel` is conditionally hidden.

```
page.tsx:
  <Sidebar />                                          ← always
  {!isAuthenticated ? <WelcomeScreen /> : <ChatPanel />}
  <FileExplorerPanel />                               ← always (empty when unauthenticated)
  {!isAuthenticated && <LoginDialog />}
  {isAuthenticated && <GuideFloatingPanel />}          ← conditional
```

**Rationale**: Sidebar provides visual context (navigation icons, branding) even when unauthenticated, reducing "what is this app?" cognitive load. The welcome screen occupies the main content area with a login call-to-action.

### D3: LoginDialog — extract from existing login page

**Choice**: New `src/components/login-dialog.tsx` component. Extracts the email+password form and VIP login dialog from the deleted `src/app/login/page.tsx`. Uses shadcn `Dialog` primitives. Controlled by `AuthStore.showLoginDialog`.

**Rationale**: Reuses existing form logic and VIP login flow. No new API calls needed — calls the same `AuthStore.login()` and `AuthStore.vipLogin()`. On success, `closeLoginDialog()` is called; `isAuthenticated` flips to true, which causes `page.tsx` to swap WelcomeScreen → ChatPanel and StreamProvider to establish SSE.

### D4: AuthStore — global login dialog state

**Choice**: Add `showLoginDialog: boolean`, `openLoginDialog()`, `closeLoginDialog()` to AuthStore.

**Rationale**: The login dialog needs to be triggerable from multiple sources: sidebar bottom bar click, welcome screen button click, and 401-with-failed-refresh. A single store-level boolean is the simplest coordination mechanism. Alternative (UI command bus event) was considered but rejected as unnecessarily indirect for a simple boolean.

### D5: authFetch 401 fallback — open dialog instead of redirect

**Choice**: When `authFetch` gets a 401 and `_doRefresh()` fails, dispatch a `CustomEvent('auth-expired')` on `window`. A listener in `AuthStore` (or `AuthGate`) calls `openLoginDialog()`.

**Rationale**: `api.ts` cannot import `AuthStore` directly (circular dependency — `auth-store.ts` imports from `api.ts` indirectly via `API_BASE_URL`). The `CustomEvent` pattern is already used by the project's `subscribeUiCommand` system. Using a lightweight DOM event avoids the circular import while maintaining decoupling.

**Alternative considered**: Move `openLoginDialog` to a standalone module (not part of AuthStore) that `api.ts` can import. Rejected — fragmenting auth state across modules violates single-source-of-truth.

### D6: BottomActionBar dual-state

**Choice**: `BottomActionBar` checks `isAuthenticated`. When `false`, renders a "登录" button (user icon + "登录" label + arrow). When `true`, renders the existing avatar + name + settings dropdown.

```
Unauthenticated:  [👤] 登录                    [→]
Authenticated:    [avatar] 用户名              [⚙️]
```

**Rationale**: Consistent placement — the bottom-left corner is always the "user/account" area, whether it's a login trigger or a profile menu. No layout shift between states.

### D7: JWT token expiry — 10 years (effectively non-expiring)

**Choice**: Change `jwt_access_token_expiry` and `jwt_refresh_token_expiry` from `3600` / `604800` to `315360000` (10 years) in `config.py`. Align cookie `max_age` in `dependencies.py`.

**Rationale**: 10 years is practically infinite for a local dev tool. Using a large finite number (instead of removing the `exp` claim) keeps JWT spec compliance and avoids changes to `verify_token()` logic.

**Alternative considered**: Remove `exp` claim entirely. Rejected — PyJWT's `decode()` skips expiry check only if `options={"verify_exp": False}` is passed, which would require changing `verify_token()`. A 10-year expiry achieves the same UX with zero code change in the JWT layer.

### D8: Session invalidation — backend restart regenerates JWT secret

**Choice**: Rely on the existing `ensure_jwt_secret()` behavior: when `JWT_SECRET` is not set in `.env`, a random secret is generated on each startup. This is already the default for dev mode.

**Rationale**: No code change needed. The behavior is:
- `JWT_SECRET` not set (default) → secret regenerated on restart → all tokens invalid → user sees WelcomeScreen
- `JWT_SECRET` explicitly set → secret persists across restarts → tokens remain valid

Users who want "restart = logout" simply don't set `JWT_SECRET`. Users who want cross-restart persistence can set it. This is already documented in `ensure_jwt_secret()`.

### D9: Register page removal — no migration needed

**Choice**: Delete `src/app/register/page.tsx` and `src/app/login/page.tsx`. The `/api/auth/register` backend endpoint is retained for admin/scripted use. `allowRegistration` config still controls API-level registration.

**Rationale**: Registration UI is unnecessary in the new flow. The primary login mechanism is email+password or VIP login. New users can be created by an administrator via API or by enabling registration temporarily.

## Risks / Trade-offs

- **[Security: unauthenticated users see UI shell]** → Backend API still requires JWT for all data endpoints; the UI shell shows no data, only layout and branding. No data leakage. The `authFetch` wrapper ensures no API call is made without a token.
- **[Token never expires = stale sessions]** → Session invalidates on backend restart (JWT secret regenerated). Manual logout clears cookie + localStorage. `token_version` field still supports global revocation (change-password / logout-all). The 10-year window is acceptable for a local-run multi-agent tool.
- **[Deep links to /login or /register break]** → Next.js will 404 on those routes. Acceptable — this is a breaking change. No external systems link to these routes.
- **[401 mid-session opens dialog over existing data]** → Existing conversation data remains in Zustand store; on re-login, SSE reconnects and data refreshes. No data loss. The dialog is non-blocking (user can dismiss it), but API calls will keep failing until re-authenticated.
- **[authFetch circular dependency with AuthStore]** → Solved via `CustomEvent('auth-expired')` DOM event, avoiding direct import. Listener is set up in `AuthGate` or a dedicated effect.
- **[WelcomeScreen visual assets]** → Reuse branding elements from `auth-brand-panel.tsx` (logo, feature highlights) before deleting it. No new design assets needed.
