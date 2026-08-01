# Replace Login Page With In-App Login Dialog

## Why

The current auth flow forces unauthenticated users to a standalone `/login` page, creating a disconnected entry experience. Users should land directly in the main workspace (in a read-only "guided" state) and log in via a dialog triggered from the bottom-left corner. Additionally, the token lifecycle (1h access / 7d refresh) causes unnecessary re-login friction — once logged in, the session should persist indefinitely until the user manually logs out or the backend restarts.

## What Changes

- **BREAKING**: Remove `/login` and `/register` standalone pages entirely
- **BREAKING**: `AuthGate` no longer redirects unauthenticated users to `/login`; it always renders children, letting downstream components handle the unauthenticated state
- New `WelcomeScreen` component shown in the main content area when unauthenticated — displays AChat branding and a "登录" call-to-action
- New `LoginDialog` component (Dialog-based) replacing the standalone login page form, including the VIP login shortcut
- `Sidebar` `BottomActionBar` shows a "登录" button when unauthenticated (replacing the avatar + settings dropdown); shows the existing avatar + dropdown when authenticated
- `AuthStore` gains `showLoginDialog` / `openLoginDialog()` / `closeLoginDialog()` state to control the login dialog globally
- `authFetch` 401-fallback: when token refresh fails, trigger `openLoginDialog()` instead of redirecting to `/login`
- `GuideFloatingPanel` hidden when unauthenticated (requires backend Agent runtime)
- **BREAKING**: JWT access/refresh token expiry changed from 1h/7d to 10 years (effectively non-expiring)
- JWT secret regenerated on every backend restart (already the default behavior when `JWT_SECRET` is not set), making all existing tokens invalid after restart
- Cookie `max_age` aligned with the new token expiry
- Remove `auth-background`, `auth-brand-panel`, and `AuthLogo` components (visual assets absorbed into `WelcomeScreen`)

## Capabilities

### New Capabilities

_(none — no new capability domains introduced)_

### Modified Capabilities

- `frontend`: Route guard behavior changes from "redirect to /login" to "render workspace in guided state"; new `LoginDialog` and `WelcomeScreen` components; `AuthStore` gains dialog state; `Sidebar` bottom bar dual-state; `authFetch` 401 fallback changes from redirect to dialog
- `user-auth`: JWT token lifecycle changes from short-lived (1h/7d) to effectively non-expiring (10 years); session invalidation via backend restart (JWT secret regeneration) or manual logout; cookie `max_age` aligned with token expiry

## Impact

- **Frontend deleted code**: `src/app/login/page.tsx`, `src/app/register/page.tsx`, `src/components/auth-background.tsx`, `src/components/auth-brand-panel.tsx`, `src/components/AuthLogo.tsx`
- **Frontend new code**: `src/components/login-dialog.tsx`, `src/components/welcome-screen.tsx`
- **Frontend modified code**: `src/components/auth-gate.tsx`, `src/stores/auth-store.ts`, `src/components/sidebar.tsx`, `src/app/page.tsx`, `src/lib/api.ts`, `src/components/guide-floating-panel.tsx`
- **Backend modified code**: `backend/app/config.py` (token expiry values), `backend/app/auth/dependencies.py` (cookie max_age)
- **Backend unchanged**: `jwt_handler.py` (exp claim logic unchanged, just larger value), `ensure_jwt_secret()` (already supports regenerate-on-restart), `/api/auth/*` endpoints (all retained)
- **Specs**: `frontend` and `user-auth` specs updated to reflect new auth flow and token lifecycle
