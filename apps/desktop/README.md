# AChat Desktop (Tauri 2)

Authoritative Windows desktop delivery for AChat:

**Tauri shell + local engine + official remote frontend/API**

This package does **not** start a local Next.js server. The window loads the configured `webUrl` from `official.json` after the local engine becomes healthy.

## Prerequisites

- Rust toolchain (stable) + MSVC Build Tools on Windows
- Node 20+ / pnpm
- Python 3.11+ for the local engine (dev) or a prebuilt `resources/engine` package

## Config flavors

| File | Use |
|---|---|
| `configs/official.dev.json` | Local web/API |
| `configs/official.staging.json` | Staging endpoints |
| `configs/official.prod.json` | Production endpoints |
| `src-tauri/official.json` | Active file packaged with the shell |

Copy a flavor over `src-tauri/official.json` before build:

```bash
cp configs/official.dev.json src-tauri/official.json
pnpm check:config
```

## Brand icons

**Scope**: desktop shell only (window / taskbar / shortcut / installer). Web tab favicon and login page are not acceptance targets for this package.

| Piece | Location |
|---|---|
| Source mark | repo `src/app/favicon.ico` |
| Generator | `python apps/desktop/scripts/generate-icons.py` (Pillow) |
| Outputs | `src-tauri/icons/*` |
| Bundle list | `src-tauri/tauri.conf.json` → `bundle.icon` |
| Runtime window icon | `src-tauri/src/lib.rs` embeds `icons/icon.png` and `set_icon` on startup |
| Cargo feature | `tauri` feature `image-png` in `src-tauri/Cargo.toml` |

Regenerate + apply:

```bash
# from repo root
python apps/desktop/scripts/generate-icons.py

# REQUIRED: rebuild the shell — icons are baked into the exe / installer
cd apps/desktop
pnpm dev          # debug
# or: pnpm build  # release installer
```

**Only replacing files under `icons/` without rebuild will leave the old logo on an already-built exe.**

Generator overwrites:

- `icon.ico` (16/24/32/48/64/128/256 PNG-compressed entries)
- `32x32.png`, `128x128.png`, `henry.w@example.net`, `icon.png`
- `icon.icns` (bundler list; Windows primarily uses ico/png)

Do **not** edit `public/agent-icons/*` for product branding — those are agent avatars.

OpenSpec: change `desktop-client-tauri-local-engine` design **D20**.

## Dev

```bash
# terminal 1: official web + cloud API as usual
# terminal 2: desktop shell
cd apps/desktop
pnpm install
pnpm dev
```

The shell spawns the engine via `python -m app.desktop.cli` / `ACHAT_ENGINE_BIN` when a packaged sidecar is absent.

## Build engine sidecar

See `backend/scripts/desktop/PACKAGING.md`.

## Related specs

OpenSpec change: `openspec/changes/desktop-client-tauri-local-engine`
