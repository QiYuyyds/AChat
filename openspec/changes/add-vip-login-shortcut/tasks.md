# Tasks: Add VIP Login Shortcut

## 1. Backend configuration and authentication

- [ ] 1.1 Add `vip_login_enabled` to backend settings and expose `vipLoginEnabled` in public auth config
- [ ] 1.2 Add a password-only VIP login request schema
- [ ] 1.3 Add service logic that authenticates the existing `DEFAULT_USER_EMAIL` user with the supplied password
- [ ] 1.4 Add `POST /api/auth/vip-login` with feature gating and generic errors
- [ ] 1.5 Reuse the existing auth response, JWT pair, and HttpOnly cookie behavior

## 2. Server-only password reset

- [ ] 2.1 Add a server script that resets the configured default user's bcrypt password hash
- [ ] 2.2 Increment `token_version` during password reset
- [ ] 2.3 Reject empty password configuration and avoid logging plaintext passwords
- [ ] 2.4 Document `VIP_LOGIN_ENABLED`, `DEFAULT_USER_EMAIL`, and `DEFAULT_USER_PASSWORD` in `backend/.env.example`

## 3. Frontend VIP login

- [ ] 3.1 Extend AuthStore config with `vipLoginEnabled` and load public auth config for unauthenticated users
- [ ] 3.2 Add an AuthStore `vipLogin(password)` action using the new endpoint
- [ ] 3.3 Add the bottom-right “VIP 登录” button without changing the existing login form
- [ ] 3.4 Add the password-only dialog with loading, error, Enter-submit, cancel, and focus behavior
- [ ] 3.5 Hide the entry when VIP login is disabled and verify responsive layout

## 4. Tests and verification

- [ ] 4.1 Add backend tests for enabled, disabled, success, invalid password, missing account, and cookie/token behavior
- [ ] 4.2 Add reset-script tests for password replacement and token invalidation
- [ ] 4.3 Add frontend tests for configuration, dialog interaction, successful redirect, and error state
- [ ] 4.4 Run targeted backend and frontend tests
- [ ] 4.5 Run `ruff check .`, `pytest`, `pnpm typecheck`, and `pnpm lint`

