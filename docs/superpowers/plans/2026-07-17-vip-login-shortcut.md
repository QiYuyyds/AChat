# VIP Login Shortcut Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional password-only VIP shortcut that authenticates the existing `admin@local` account without changing its permissions or exposing its email/password to the frontend.

**Architecture:** The backend exposes a feature-gated `/api/auth/vip-login` endpoint that resolves `DEFAULT_USER_EMAIL` and delegates to the existing bcrypt/JWT response path. The login page reads a public boolean flag, opens a password-only dialog, and delegates session state to AuthStore. A server-only script resets the default user's password and invalidates existing sessions.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, bcrypt, PyJWT, pytest, Next.js 16.2.6 App Router, React 19, TypeScript, Zustand, Base UI Dialog, Vitest.

---

## File Structure

- Modify `backend/app/config.py`: add the `vip_login_enabled` setting.
- Modify `backend/app/schemas/requests.py`: define `VipLoginRequest`.
- Modify `backend/app/schemas/__init__.py`: export the new request model.
- Modify `backend/app/auth/service.py`: authenticate the configured default account without exposing its email.
- Modify `backend/app/api/auth.py`: expose the feature flag and VIP endpoint.
- Modify `backend/tests/test_auth.py`: cover endpoint configuration, success, failure, and cookie behavior.
- Create `backend/scripts/reset_default_user_password.py`: server-only bcrypt reset and token invalidation.
- Create `backend/tests/test_reset_default_user_password.py`: cover successful and rejected reset operations.
- Modify `backend/.env.example`: document the feature switch and initial credentials.
- Modify `src/stores/auth-store.ts`: load public auth config and add `vipLogin(password)`.
- Create `src/stores/auth-store.test.ts`: verify public config and VIP login state transitions.
- Modify `src/app/login/page.tsx`: add the bottom-right trigger and password-only dialog.
- Modify `openspec/changes/add-vip-login-shortcut/tasks.md`: mark completed tasks as verification succeeds.

### Task 1: Backend VIP authentication API

**Files:**
- Modify: `backend/tests/test_auth.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/schemas/requests.py`
- Modify: `backend/app/schemas/__init__.py`
- Modify: `backend/app/auth/service.py`
- Modify: `backend/app/api/auth.py`

- [ ] **Step 1: Write failing endpoint tests**

Add tests that set `VIP_LOGIN_ENABLED=true` and `DEFAULT_USER_EMAIL` to the fixture user's email, clear `get_settings()` after each environment change, then assert:

```python
async def test_vip_login_success(raw_client, test_user, monkeypatch):
    monkeypatch.setenv("VIP_LOGIN_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_USER_EMAIL", test_user["email"])
    get_settings.cache_clear()
    response = await raw_client.post(
        "/api/auth/vip-login", json={"password": "testpass123"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == test_user["email"]
    assert COOKIE_NAME in response.cookies
```

Also cover disabled (404), invalid password (401), missing account (401), and `GET /api/auth/config` returning `vipLoginEnabled`.

- [ ] **Step 2: Run tests and confirm red state**

Run: `pytest tests/test_auth.py -q`

Expected: FAIL because `/api/auth/vip-login` and `vipLoginEnabled` do not exist.

- [ ] **Step 3: Add configuration and request schema**

Add to `Settings`:

```python
vip_login_enabled: bool = False
```

Add and export:

```python
class VipLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
```

- [ ] **Step 4: Add service and route implementation**

Add a service function that reuses `_user_profile()` and `_tokens_for_user()`:

```python
async def authenticate_default_user(db: AsyncSession, password: str) -> AuthResult:
    email = get_settings().default_user_email
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Invalid credentials")
    return AuthResult(user=_user_profile(user), tokens=_tokens_for_user(user))
```

Extend `_config_response()` with `vipLoginEnabled`. Add `POST /auth/vip-login`; return 404 when disabled, parse `VipLoginRequest`, return generic 401 on all credential failures, and use `_auth_response()` on success.

- [ ] **Step 5: Run endpoint tests and lint**

Run: `pytest tests/test_auth.py -q`

Expected: PASS.

Run: `ruff check app/api/auth.py app/auth/service.py app/schemas tests/test_auth.py`

Expected: PASS.

- [ ] **Step 6: Commit backend API**

```bash
git add backend/app/config.py backend/app/schemas/requests.py backend/app/schemas/__init__.py backend/app/auth/service.py backend/app/api/auth.py backend/tests/test_auth.py
git commit -m "feat(auth): add VIP login endpoint"
```

### Task 2: Server-only password reset

**Files:**
- Create: `backend/scripts/reset_default_user_password.py`
- Create: `backend/tests/test_reset_default_user_password.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: Write failing reset tests**

Create a fixture-backed test that seeds the default user, changes `DEFAULT_USER_PASSWORD`, runs `reset_default_user_password()`, and verifies both bcrypt validation and `token_version == 1`. Add a second test asserting an empty configured password raises `ValueError` without changing the row.

```python
assert verify_password("654321", user.password_hash)
assert user.token_version == 1
```

- [ ] **Step 2: Run tests and confirm red state**

Run: `pytest tests/test_reset_default_user_password.py -q`

Expected: FAIL because the script module does not exist.

- [ ] **Step 3: Implement the reset script**

Create an async function and CLI entry point:

```python
async def reset_default_user_password() -> None:
    settings = get_settings()
    if not settings.default_user_password:
        raise ValueError("DEFAULT_USER_PASSWORD must not be empty")
    async with get_db() as db:
        result = await db.execute(
            select(User).where(User.email == settings.default_user_email)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError("Default user not found")
        user.password_hash = hash_password(settings.default_user_password)
        user.token_version += 1
        user.updated_at = now_ms()
```

The `__main__` path initializes the DB, runs the function, and prints only a success message containing the email—not the password.

- [ ] **Step 4: Document configuration**

Document:

```env
VIP_LOGIN_ENABLED=true
DEFAULT_USER_EMAIL=admin@local
DEFAULT_USER_PASSWORD=123456
```

Clarify that changing `.env` does not update an existing hash until the reset script runs.

- [ ] **Step 5: Run reset tests and lint**

Run: `pytest tests/test_reset_default_user_password.py -q`

Expected: PASS.

Run: `ruff check scripts/reset_default_user_password.py tests/test_reset_default_user_password.py`

Expected: PASS.

- [ ] **Step 6: Commit reset workflow**

```bash
git add backend/scripts/reset_default_user_password.py backend/tests/test_reset_default_user_password.py backend/.env.example
git commit -m "feat(auth): add server-side default password reset"
```

### Task 3: AuthStore support

**Files:**
- Create: `src/stores/auth-store.test.ts`
- Modify: `src/stores/auth-store.ts`

- [ ] **Step 1: Write failing store tests**

Mock `global.fetch` and assert that unauthenticated initialization requests `/api/auth/config`, stores `vipLoginEnabled`, and that `vipLogin('123456')` posts only `{ password: '123456' }`, stores the access token, and sets the authenticated user.

```typescript
expect(fetchMock).toHaveBeenCalledWith(
  expect.stringContaining('/api/auth/vip-login'),
  expect.objectContaining({ body: JSON.stringify({ password: '123456' }) }),
)
```

- [ ] **Step 2: Run tests and confirm red state**

Run: `pnpm test -- src/stores/auth-store.test.ts`

Expected: FAIL because `vipLoginEnabled` and `vipLogin()` do not exist.

- [ ] **Step 3: Implement public config loading and VIP login**

Extend the store types:

```typescript
interface AuthConfig {
  allowRegistration: boolean
  vipLoginEnabled: boolean
}

vipLogin: (password: string) => Promise<void>
```

When `/api/auth/me` is unauthenticated, fetch `/api/auth/config` and retain both booleans. Implement VIP login with the same token storage and state update path as standard login, but post only the password.

- [ ] **Step 4: Run store tests and typecheck**

Run: `pnpm test -- src/stores/auth-store.test.ts`

Expected: PASS.

Run: `pnpm typecheck`

Expected: PASS.

- [ ] **Step 5: Commit store support**

```bash
git add src/stores/auth-store.ts src/stores/auth-store.test.ts
git commit -m "feat(auth): add VIP login store action"
```

### Task 4: Login-page dialog

**Files:**
- Modify: `src/app/login/page.tsx`

- [ ] **Step 1: Read the bundled Next.js 16.2.6 client-component and forms guidance**

Read the relevant files under `node_modules/next/dist/docs/` before editing. Preserve the existing client page and router behavior; do not introduce deprecated navigation or form APIs.

- [ ] **Step 2: Add dialog state and submit handler**

Add `vipOpen`, `vipPassword`, `vipError`, and `vipSubmitting` state. The handler calls `vipLogin(vipPassword)`, closes the dialog on success, and routes to `/`. Clear the password whenever the dialog closes.

- [ ] **Step 3: Add trigger and dialog**

Use the existing Base UI wrappers from `@/components/ui/dialog`. Render the trigger only when `config.vipLoginEnabled` is true. Place it with `absolute bottom-4 right-4 z-20` inside the existing right-side `relative` container. The dialog contains only title, password field, cancel, error, and submit controls; it must not contain administrator, demo, or experience-space wording.

- [ ] **Step 4: Verify UI behavior**

Run: `pnpm typecheck`

Expected: PASS.

Run: `pnpm lint`

Expected: PASS with no new lint errors.

Manually verify desktop and narrow widths: original form unchanged, trigger does not overlap the card, Enter submits once, errors remain in the dialog, and close clears the password.

- [ ] **Step 5: Commit UI**

```bash
git add src/app/login/page.tsx
git commit -m "feat(ui): add VIP login shortcut dialog"
```

### Task 5: OpenSpec completion and full verification

**Files:**
- Modify: `openspec/changes/add-vip-login-shortcut/tasks.md`

- [ ] **Step 1: Run targeted verification**

```bash
cd backend
pytest tests/test_auth.py tests/test_reset_default_user_password.py -q
ruff check app/api/auth.py app/auth/service.py app/schemas scripts/reset_default_user_password.py tests/test_auth.py tests/test_reset_default_user_password.py
cd ..
pnpm test -- src/stores/auth-store.test.ts
pnpm typecheck
pnpm lint
```

Expected: all commands exit 0.

- [ ] **Step 2: Run broader authentication regression tests**

Run: `cd backend && pytest tests/test_auth.py tests/test_auth_isolation.py tests/test_sse_auth.py tests/test_csrf.py -q`

Expected: PASS.

- [ ] **Step 3: Mark OpenSpec tasks complete**

Mark only verified checklist items complete in `openspec/changes/add-vip-login-shortcut/tasks.md`.

- [ ] **Step 4: Commit task status**

```bash
git add openspec/changes/add-vip-login-shortcut/tasks.md
git commit -m "spec(auth): complete VIP login shortcut tasks"
```

