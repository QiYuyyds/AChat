# Deprecated: Electron desktop path

This directory is **not** the authoritative AChat desktop delivery path.

Use instead:

- `apps/desktop/` — Tauri 2 Windows shell
- `backend/app/desktop/` — local engine runtime
- `src/shared/desktop/` — frontend bridge types
- OpenSpec change: `openspec/changes/desktop-client-tauri-local-engine`

The Electron + embedded Next approach is superseded and should not receive new features.
