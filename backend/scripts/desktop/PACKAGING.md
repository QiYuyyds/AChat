# Desktop local engine packaging (Windows)

## Decision (task 2.6)

**v1 choice: PyInstaller one-folder** (`achat-engine/` directory with `achat-engine.exe` + deps).

| Option | Pros | Cons | v1 |
|---|---|---|---|
| Embeddable CPython + venv | Smaller incremental updates | Fragile native wheels, more install scripts | later |
| **PyInstaller one-folder** | Reproducible, bundles native deps, single build script | Larger folder (~hundreds of MB) | **selected** |
| Nuitka | Faster runtime | Longer build, more ops cost | not v1 |

## Build

From a Windows machine with the project backend venv:

```powershell
.\backend\scripts\desktop\build_engine_windows.ps1
```

Output is copied to:

`apps/desktop/src-tauri/resources/engine/`

The Tauri bundler includes that directory as package resources.

## Runtime CLI

```text
achat-engine.exe serve \
  --bind 127.0.0.1 \
  --port 0 \
  --data-dir %APPDATA%\AChat \
  --engine-token <session> \
  --official-api-url https://api.example \
  --allowed-origins https://app.example
```

Prints `ENGINE_PORT=<n>` on stdout for the shell handshake.

## Non-goals

- Do not bundle Claude/Codex CLIs.
- Do not ship Postgres/Milvus connection strings for end users.
