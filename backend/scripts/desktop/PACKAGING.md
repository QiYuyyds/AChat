# Desktop local engine packaging (Windows)

## Decision

**v1: PyInstaller one-folder** (`resources/engine/` with `achat-engine.exe` + deps).

| Option | Pros | Cons | v1 |
|---|---|---|---|
| Embeddable CPython + venv | Smaller incremental updates | Fragile native wheels | later |
| **PyInstaller one-folder** | Reproducible, bundles native deps | Larger folder | **selected** |

## Build engine sidecar

From repo root (Windows, `backend\.venv` recommended):

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\desktop\build_engine_windows.ps1
```

Output:

`apps/desktop/src-tauri/resources/engine/achat-engine.exe`

## Runtime CLI (v1)

```text
achat-engine.exe serve ^
  --bind 127.0.0.1 ^
  --port 0 ^
  --data-dir %APPDATA%\AChat ^
  --engine-token <session> ^
  --infra-config <path-to-infra.default.json> ^
  [--ui-dir <path-to-static-ui>] ^
  [--allowed-origins http://127.0.0.1:...] ^
  [--official-api-url ...]   REM optional legacy
```

Prints `ENGINE_PORT=<n>` on stdout for the shell handshake.

Loads `backend/.env` (or packaged env) for `JWT_SECRET` when present; otherwise uses a stable secret under `%APPDATA%\AChat\config\jwt.secret`.

## Full product installer (shell + engine + **full UI** + config)

Users must get the **same full AChat UI** as `pnpm dev` (login, chat, SSE) — not the engine placeholder page.

```powershell
# From repo root

# 1) Engine sidecar
powershell -ExecutionPolicy Bypass -File backend\scripts\desktop\build_engine_windows.ps1

# 2) Full Next static UI → out/  then copy into resources/ui
pnpm desktop:build-ui
cd apps\desktop
pnpm build:ui
# Verify: src-tauri\resources\ui\index.html must NOT contain「引擎占位页」
# Prefer seeing _next\ or a non-placeholder index.html
Get-Content .\src-tauri\resources\ui\.achat-ui-build.json

# 3) infra for your testers (do not commit production passwords)
pnpm check:infra
# Edit src-tauri\infra.default.json databaseUrl if needed

# 4) Tauri NSIS
pnpm build
```

One-shot (root; still requires engine already built once if missing):

```powershell
pnpm desktop:package
```

Installer artifact (typical):

`apps/desktop/src-tauri/target/release/bundle/nsis/AChat_*_x64-setup.exe`

### Placeholder-only (internal engine smoke — NOT for end users)

```powershell
cd apps\desktop
pnpm build:ui:placeholder
pnpm build
```

## Known packaging pitfalls (Windows)

### WinError 10106 / empty “Unhandled exception” / health check timeout

If the shell shows `local engine health check timed out` and `%APPDATA%\AChat\logs\desktop.log` contains:

```text
File "app\desktop\runtime.py", line …, in allocate_port
  File "socket.py", …
OSError: [WinError 10106] …
```

**Cause:** packaging from **Anaconda/Conda** CPython lets PyInstaller copy private UCRT forwarders (`api-ms-win-*.dll`, `ucrtbase.dll`) into `resources/engine/_internal`. Those stubs break Winsock inside the frozen process (`socket.socket()` fails before uvicorn binds).

**Fix (already in `build_engine_windows.ps1`):** after copy, **delete** those DLLs so the engine uses the system UCRT. The script also runs a frozen `serve` smoke that must print `ENGINE_PORT=`.

Prefer building the engine with **python.org** CPython + venv when possible; Conda works only with the strip step above.

### Older `_ssl` ImportError

If you see `DLL load failed while importing _ssl`, the script’s SSL DLL copy step (libssl/libcrypto from `sys.base_prefix`) is required — do not skip it on Conda builds.

## Non-goals

- Do not bundle Claude/Codex CLIs.
- Do not embed full Postgres/Milvus *processes* in the package.
- Product builds **must** ship full static frontend in `resources/ui` (`pnpm desktop:build-ui` + `pnpm build:ui`).
- Placeholder UI is for internal smoke only (`ACHAT_UI_ALLOW_PLACEHOLDER=1` / `build:ui:placeholder`).
