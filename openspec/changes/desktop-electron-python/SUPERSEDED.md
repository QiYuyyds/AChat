# SUPERSEDED

This change is **superseded** by:

`openspec/changes/desktop-client-tauri-local-engine`

## Why

`desktop-electron-python` assumed Electron would re-host Next (or a dual-process stack) and/or ask users for infrastructure connection strings. The product decision is now:

- Tauri 2 Windows shell
- Local engine for Agent/tools/workspace/offline SQLite
- Official **remote** frontend URL + official HTTPS API
- No end-user Postgres/Milvus connection wizard for the default path

Do **not** continue implementing tasks under this directory. Treat any remaining Electron packaging as legacy; new desktop work targets `apps/desktop/` and the Tauri local-engine change.
