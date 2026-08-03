## 1. Backend: JWT Token Lifecycle

- [x] 1.1 Update `backend/app/config.py` — change `jwt_access_token_expiry` from `3600` to `315360000` (10 years)
- [x] 1.2 Update `backend/app/config.py` — change `jwt_refresh_token_expiry` from `604800` to `315360000` (10 years)
- [x] 1.3 Update `backend/app/auth/dependencies.py` — align cookie `max_age` in `set_auth_cookie()` with new `jwt_access_token_expiry` value (already reads from settings, verify no hardcoded override)
- [x] 1.4 Verify `ensure_jwt_secret()` in `backend/app/config.py` — confirm default behavior (no `JWT_SECRET` set) regenerates secret on each restart; no code change needed unless logic is incorrect
- [x] 1.5 Run `ruff check .` and `pytest backend/tests/test_auth.py` to verify backend changes don't break existing auth tests

## 2. Frontend: AuthStore — Add Login Dialog State

- [x] 2.1 Add `showLoginDialog: boolean` field to `AuthState` interface in `src/stores/auth-store.ts` (default `false`)
- [x] 2.2 Add `openLoginDialog()` and `closeLoginDialog()` actions to the store
- [x] 2.3 In `logout()` action — after clearing auth state, do NOT redirect (remove any redirect logic if present); the UI will handle the transition via conditional rendering
- [x] 2.4 In `initialize()` — when `/api/auth/me` fails, set `isAuthenticated=false` (do NOT redirect); the UI renders the guided state
- [x] 2.5 Add a `window` event listener for `CustomEvent('auth-expired')` that calls `openLoginDialog()` and sets `isAuthenticated=false`; set up this listener in `initialize()` or in `AuthGate`

## 3. Frontend: AuthGate — Remove Route Redirects

- [x] 3.1 Remove `PUBLIC_ROUTES` constant and `isPublicRoute` logic from `src/components/auth-gate.tsx`
- [x] 3.2 Remove the `router.replace('/login')` and `router.replace('/')` redirect effects
- [x] 3.3 Simplify `AuthGate` to: call `initialize()` on mount → show loading spinner while `isLoading` → always render `children` regardless of auth state
- [x] 3.4 Keep the `setUserId(user?.id ?? null)` sync with AppStore (unchanged)

## 4. Frontend: LoginDialog Component

- [x] 4.1 Create `src/components/login-dialog.tsx` — a shadcn `Dialog` controlled by `AuthStore.showLoginDialog`
- [x] 4.2 Extract email + password form logic from `src/app/login/page.tsx` into the dialog (same input classes, same `AuthStore.login()` call)
- [x] 4.3 Extract VIP login dialog logic from `src/app/login/page.tsx` — include the VIP button (conditionally shown when `config.vipLoginEnabled`) and VIP password sub-dialog
- [x] 4.4 On successful login/vipLogin — call `closeLoginDialog()` (AuthStore sets `isAuthenticated=true`, which triggers UI transition)
- [x] 4.5 On login error — show error message in the dialog (same as current login page behavior)
- [x] 4.6 Dialog should be closable (X button / click outside) — closing sets `showLoginDialog=false` without authenticating

## 5. Frontend: WelcomeScreen Component

- [x] 5.1 Create `src/components/welcome-screen.tsx` — renders in main content area when unauthenticated
- [x] 5.2 Include AChat branding/logo (reuse visual elements from `auth-brand-panel.tsx` before deleting it)
- [x] 5.3 Include a brief feature highlight (Agent 协作 / 产物预览 / 知识图谱 — reuse icons from deleted register page)
- [x] 5.4 Include a prominent "立即登录" button that calls `AuthStore.openLoginDialog()`
- [x] 5.5 Match the visual style of the existing empty states (centered content, muted icon, `bg-background/80 backdrop-blur`)

## 6. Frontend: Sidebar BottomActionBar Dual-State

- [x] 6.1 In `src/components/sidebar.tsx` `BottomActionBar` — check `isAuthenticated` from AuthStore
- [x] 6.2 When `!isAuthenticated` — render a "登录" button (User icon + "登录" label + ChevronRight icon) that calls `openLoginDialog()`
- [x] 6.3 When `isAuthenticated` — render the existing avatar + name + settings dropdown (unchanged)
- [x] 6.4 Ensure no layout shift between the two states (same height, same border-top, same padding)

## 7. Frontend: page.tsx Conditional Rendering

- [x] 7.1 In `src/app/page.tsx` — read `isAuthenticated` from AuthStore
- [x] 7.2 When `!isAuthenticated` — render `<WelcomeScreen />` in place of `<ChatPanel />` (keep Sidebar and FileExplorerPanel rendered)
- [x] 7.3 Conditionally hide `<GuideFloatingPanel />` when `!isAuthenticated`
- [x] 7.4 Render `<LoginDialog />` always (it controls its own visibility via `showLoginDialog`)

## 8. Frontend: api.ts 401 Fallback

- [x] 8.1 In `src/lib/api.ts` `authFetch` — when 401 and `_doRefresh()` fails, dispatch `window.dispatchEvent(new CustomEvent('auth-expired'))` before returning the 401 response
- [x] 8.2 Do NOT import AuthStore in `api.ts` (avoid circular dependency) — use the DOM event for decoupled communication
- [x] 8.3 Verify the event listener set up in task 2.5 receives the event and opens the dialog

## 9. Frontend: Delete Old Auth Pages and Components

- [x] 9.1 Delete `src/app/login/page.tsx` and the `src/app/login/` directory
- [x] 9.2 Delete `src/app/register/page.tsx` and the `src/app/register/` directory
- [x] 9.3 Delete `src/components/auth-background.tsx` (no longer used after WelcomeScreen is self-contained)
- [x] 9.4 Delete `src/components/auth-brand-panel.tsx` (visual assets absorbed into WelcomeScreen)
- [x] 9.5 Delete `src/components/AuthLogo.tsx` if not referenced elsewhere (check imports first) — KEPT (still used by login-dialog.tsx and welcome-screen.tsx)
- [x] 9.6 Verify no remaining imports of deleted files (run `pnpm typecheck` to catch dangling references)

## 10. Frontend: Sidebar Navigation Disabled State (Optional Polish)

- [x] 10.1 When `!isAuthenticated`, visually disable sidebar navigation buttons (conversations, agents, etc.) with reduced opacity / `pointer-events-none`
- [x] 10.2 Clicking disabled navigation buttons triggers `openLoginDialog()` instead of attempting API calls
- [x] 10.3 The "新建对话" button and search trigger are disabled / redirect to login dialog when unauthenticated

## 11. Spec & Documentation Update

- [x] 11.1 Update `openspec/specs/frontend/spec.md` — sync with the delta spec after implementation
- [x] 11.2 Update `openspec/specs/user-auth/spec.md` — sync with the delta spec after implementation
- [x] 11.3 Update `backend/.env.example` — update comments for `JWT_ACCESS_TOKEN_EXPIRY` and `JWT_REFRESH_TOKEN_EXPIRY` to reflect 10-year default
- [x] 11.4 Verify CLAUDE.md §5.5 (auth section) doesn't contradict the new token lifecycle; update if needed

## 12. Testing

- [ ] 12.1 Manual test: start backend without `JWT_SECRET` → login → restart backend → verify `/api/auth/me` returns 401 and LoginDialog appears
- [ ] 12.2 Manual test: start backend with `JWT_SECRET` set → login → restart backend → verify session persists
- [ ] 12.3 Manual test: unauthenticated state — verify WelcomeScreen, Sidebar login button, disabled navigation, no API calls in network tab
- [ ] 12.4 Manual test: LoginDialog — email+password login, VIP login, error states, dialog close/reopen
- [ ] 12.5 Manual test: session expiry simulation (clear cookie in devtools) — verify LoginDialog opens over existing workspace
- [x] 12.6 Run `pnpm typecheck` and `pnpm lint` to verify no type errors or lint issues
- [x] 12.7 Run `ruff check .` and `pytest` on backend to verify auth tests still pass with new expiry values
