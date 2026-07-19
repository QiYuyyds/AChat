# Desktop v1 smoke matrix (pivot)

## Automated (CI / local)

- [x] Backend: `pytest tests/test_desktop_config.py tests/test_desktop_static_and_middleware.py tests/test_desktop_auth_local.py tests/test_desktop_offline_store.py tests/test_desktop_engine_auth.py`
- [x] Config check: `cd apps/desktop && pnpm check:infra`
- [x] UI build script: `cd apps/desktop && pnpm build:ui`
- [ ] Full `pnpm typecheck` at repo root (baseline may already have unrelated failures; desktop modules should not add new ones)

## Manual

### 21.1 Web-only

- Launch Next + FastAPI without Tauri; no `window.achatDesktop`
- Login / chat / stream work against `NEXT_PUBLIC_API_BASE_URL`

### 21.2 Desktop online (default official infra)

1. Ensure `src-tauri/infra.default.json` points at reachable PG
2. `pnpm build:ui && pnpm dev` in `apps/desktop`
3. App opens local UI origin after engine ready
4. Register/login on local engine
5. Send message, observe SSE
6. Settings → provider keys save
7. CLI agent missing → clear error

### 21.3 Offline / weak net

1. After login, stop PG or break network
2. Continue conversation if session cached → outbox
3. Restore PG → `POST /api/desktop/sync/flush` or auto flush → conflicts visible, no silent overwrite

### 21.4 Custom infra override

1. Settings → 服务器 → fill custom Database URL → save
2. Restart local engine
3. Smoke login/CRUD against new endpoint
4. Revert to defaults → restart

### 20.3 Install smoke

Install NSIS build → launch → local login → message → bind folder → optional custom infra → quit cleanly.
