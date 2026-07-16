# Desktop Electron (Delta)

## Purpose

Modifies the existing `desktop-electron` capability to reflect the new Python + Next.js dual-process architecture, replacing the old Node.js full-stack model. Only changed requirements are listed here; unchanged requirements from the existing spec remain in effect.

## Requirements

### Requirement: ~~Desktop build SHALL use Next standalone output~~ → Desktop SHALL run Python FastAPI + Next.js standalone as separate subprocesses

**BREAKING**: The previous requirement "Desktop build SHALL use Next standalone output" assumed the Next.js standalone server contained all backend logic. Now the backend is a separate Python FastAPI process, and Next.js only serves the frontend.

#### Scenario: Packaged app starts
- **WHEN** Electron launches
- **THEN** Python FastAPI and Next.js standalone are spawned as separate child processes
- **AND** Next.js rewrites `/api/*` and `/deployments/*` to the Python FastAPI port
- **AND** `.next/standalone` is still available as a real unpacked directory

### Requirement: ~~Native and SDK packages SHALL remain external~~ → Python backend dependencies SHALL be distributed as pre-built wheels

**BREAKING**: The previous requirement about `serverExternalPackages` (better-sqlite3, claude-agent-sdk, codex-sdk) was specific to the Node.js backend. Now Python native dependencies are managed via wheels.

#### Scenario: Codex SDK runtime in desktop
- **WHEN** the desktop app is built
- **THEN** `@openai/codex-sdk` and `@openai/codex` are still in `serverExternalPackages` for the Next.js process
- **AND** the Python `backend/requirements.txt` is satisfied by pre-built wheel files

### Requirement: Desktop data SHALL live in userData

This requirement is UNCHANGED from the existing spec, but the semantics shift:
- Previously: `AGENTHUB_DATA_DIR` pointed to a directory containing SQLite DB files
- Now: `AGENTHUB_DATA_DIR` points to a directory containing `workspaces/`, `server.json`, `jwt_secret.txt`, `.pip_installed`, and PID files
- Database data lives on the remote PostgreSQL server, not locally

#### Scenario: Desktop app starts in production
- **WHEN** the Python server bootstraps
- **THEN** `AGENTHUB_DATA_DIR` points at `<userData>/data`
- **AND** `DATABASE_URL` points at the remote PostgreSQL server from `server.json`
- **AND** local workspace files are stored under `<AGENTHUB_DATA_DIR>/workspaces/`

### Requirement: ~~API key storage SHALL match web local mode~~ → API key storage SHALL use remote user_settings table

**BREAKING**: The previous requirement specified SQLite `app_settings` key storage. With remote PostgreSQL, API keys are stored in the `user_settings` table on the remote database, consistent with the web version.

#### Scenario: User saves an Anthropic key on desktop
- **WHEN** settings are saved
- **THEN** the key is stored in the remote PostgreSQL `user_settings` table via the Python backend
- **AND** the key is accessible from both desktop and web sessions

### Requirement: Desktop SHALL expose native file dialog via preload

New requirement. The desktop app MUST expose a `pickDirectory()` API via Electron preload for selecting local directories to bind as workspace.

#### Scenario: User binds a local directory
- **WHEN** the user creates a conversation and clicks "Bind Directory"
- **THEN** a native OS file dialog opens
- **AND** the selected path is validated with `is_path_safe()` on the Python backend
- **AND** the path is set as `boundPath` for the conversation workspace

#### Scenario: Frontend detects desktop mode
- **WHEN** the frontend JavaScript runs
- **THEN** `window.electronAPI?.isDesktop()` returns `true`
- **AND** the directory picker uses the native dialog instead of the web-based `/api/fs/listdir` browser

### Requirement: Desktop packaging SHALL NOT include better-sqlite3 native bindings

**BREAKING**: The previous spec had extensive sections about better-sqlite3 ABI management (Node ABI vs Electron ABI). With the Python backend, better-sqlite3 is no longer used.

#### Scenario: Build process
- **WHEN** `pnpm electron:build` runs
- **THEN** no better-sqlite3 native binding is included in the package
- **AND** no ABI preflight scripts are needed for better-sqlite3

### Requirement: Desktop packaging SHALL include Python embed + wheels + backend source

New requirement. The installation package MUST include the embedded Python runtime, pre-built wheel files, and the Python backend source code.

#### Scenario: Package contents
- **WHEN** the installation package is created
- **THEN** the following directories are included:
  - `python/` — embedded Python 3.11 runtime
  - `wheels/` — pre-built `.whl` files for offline pip install
  - `backend/` — Python backend source code (`app/`, `requirements.txt`, `pyproject.toml`)
  - `node/` — standalone Node.js runtime for Next.js
  - `next-standalone/` — Next.js standalone output

### Requirement: Desktop packaging SHALL include Codex runtime dependencies

This requirement is UNCHANGED from the existing spec. The Codex SDK is still used by the Python backend's Codex adapter, but it runs in the Next.js process context.

#### Scenario: User runs Codex agent from packaged app
- **WHEN** CodexAdapter starts a thread
- **THEN** the SDK can locate its platform Codex binary dependency
