# Tasks: Add VIP Login Shortcut

## 1. Backend configuration and authentication

- [x] 1.1 Add `vip_login_enabled` to backend settings and expose `vipLoginEnabled` in public auth config
- [x] 1.2 Add a password-only VIP login request schema
- [x] 1.3 Add service logic that authenticates the existing `DEFAULT_USER_EMAIL` user with the supplied password
- [x] 1.4 Add `POST /api/auth/vip-login` with feature gating and generic errors
- [x] 1.5 Reuse the existing auth response, JWT pair, and HttpOnly cookie behavior

## 2. Server-only password reset

- [x] 2.1 Add a server script that resets the configured default user's bcrypt password hash
- [x] 2.2 Increment `token_version` during password reset
- [x] 2.3 Reject empty password configuration and avoid logging plaintext passwords
- [x] 2.4 Document `VIP_LOGIN_ENABLED`, `DEFAULT_USER_EMAIL`, and `DEFAULT_USER_PASSWORD` in `backend/.env.example`

## 3. Frontend VIP login

- [x] 3.1 Extend AuthStore config with `vipLoginEnabled` and load public auth config for unauthenticated users
- [x] 3.2 Add an AuthStore `vipLogin(password)` action using the new endpoint
- [x] 3.3 Add the bottom-right “VIP 登录” button without changing the existing login form
- [x] 3.4 Add the password-only dialog with loading, error, Enter-submit, cancel, and focus behavior
- [x] 3.5 Hide the entry when VIP login is disabled and verify responsive layout

## 4. Tests and verification

- [x] 4.1 Add backend tests for enabled, disabled, success, invalid password, missing account, and cookie/token behavior
- [x] 4.2 Add reset-script tests for password replacement and token invalidation
- [ ] 4.3 Add frontend tests for configuration, dialog interaction, successful redirect, and error state
- [x] 4.4 Run targeted backend and frontend tests
- [ ] 4.5 Run `ruff check .`, `pytest`, `pnpm typecheck`, and `pnpm lint`

## Verification Notes

- Backend authentication regression: 36 passed.
- Frontend Vitest regression: 50 passed.
- Targeted ESLint for changed frontend files: 0 errors; the login page retains one pre-existing `<img>` warning.
- Full `pnpm typecheck` remains blocked by pre-existing mobile dependency and message `hidden` typing errors outside this change.
- Full `pnpm lint` remains blocked by three pre-existing errors outside this change.
- Full `ruff check .` reports the repository's existing lint backlog; targeted Ruff checks for changed backend files pass.
