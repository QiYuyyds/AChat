## ADDED Requirements

### Requirement: Desktop shell SHALL be a Tauri 2 Windows application

AChat desktop delivery MUST use a Tauri 2 shell on Windows as the OS window host. The shell MUST provide the application window, lifecycle, and native OS integrations. The shell MUST NOT embed business Agent logic or replace the AChat frontend application.

#### Scenario: User launches installed app
- **WHEN** the user starts the installed AChat desktop application on Windows
- **THEN** a Tauri-hosted native window is created
- **AND** the shell begins local engine startup before or while presenting the UI surface.

### Requirement: Shell SHALL open the official deployed frontend

The shell MUST navigate the window to a build-time or package-configured official frontend URL (`OFFICIAL_WEB_URL`). The shell MUST NOT start a local Next.js server as the primary UI host in v1.

#### Scenario: Engine becomes ready
- **WHEN** the local engine health check succeeds
- **THEN** the shell loads `OFFICIAL_WEB_URL` in the application webview
- **AND** the visible product UI is the same AChat frontend served by the official deployment.

### Requirement: Shell SHALL inject the desktop bridge object

After creating the webview context for the official frontend, the shell MUST inject `window.achatDesktop` with at least: `isDesktop=true`, `engineBaseUrl`, `engineToken`, `appVersion`, `selectDirectory()`, `getEngineStatus()`, and `restartEngine()`.

#### Scenario: Frontend detects desktop mode
- **WHEN** the official frontend script reads `window.achatDesktop`
- **THEN** `isDesktop` is true
- **AND** `engineBaseUrl` points at the loopback engine HTTP origin for this session
- **AND** `engineToken` is a non-empty session secret.

### Requirement: Shell SHALL enforce single-instance behavior

Launching a second instance MUST focus the existing window instead of starting a second full stack when a single-instance lock is held.

#### Scenario: Second launch while app is running
- **WHEN** the user starts AChat.exe while an instance is already running
- **THEN** no second local engine is required to remain running as a duplicate primary instance
- **AND** the existing window is focused.

### Requirement: Shell SHALL manage local engine lifecycle

The shell MUST spawn the local engine process, wait for health readiness, surface startup failures, and terminate the engine on application quit. The shell MUST pass data directory, bind address `127.0.0.1`, selected port or port `0`, and engine token to the engine.

#### Scenario: Engine fails to start
- **WHEN** the engine does not become healthy within the configured timeout
- **THEN** the shell shows a diagnostic error state with log location guidance
- **AND** does not silently present a broken main workspace as if online.

#### Scenario: User quits the app
- **WHEN** the user exits the desktop application
- **THEN** the shell requests graceful engine shutdown
- **AND** waits briefly for flush before force-killing if necessary.

### Requirement: Shell SHALL provide native directory selection

The shell MUST expose a native folder picker through `window.achatDesktop.selectDirectory()` for binding local workspaces on Windows.

#### Scenario: User picks a project folder
- **WHEN** the frontend calls `selectDirectory()` in desktop mode
- **THEN** a native Windows folder dialog is shown
- **AND** the selected absolute path is returned to the frontend, or null if cancelled.

### Requirement: Shell SHALL support whole-package auto-update entrypoints

The shell MUST include an auto-update mechanism that can check for and apply a whole-package update of shell and bundled engine artifacts. v1 MAY ship without code signing.

#### Scenario: Update is available
- **WHEN** the updater finds a newer package on the configured update feed
- **THEN** the user can install the whole-package update through the desktop app flow
- **AND** both shell and engine bits from that package are updated together in v1.

### Requirement: Shell window and package icons SHALL use the product brand mark

The Tauri shell MUST use product brand icons under `apps/desktop/src-tauri/icons`, derived from `src/app/favicon.ico` (or its in-repo successor). Placeholder or corrupt scaffold icons MUST NOT ship.

The shell MUST:

1. List the icon files in `tauri.conf.json` `bundle.icon` (including at least `icon.png` and multi-size `icon.ico` for Windows)
2. Rebuild so icons are embedded in the binary/installer resources — changing files on disk without rebuild is not sufficient
3. At runtime, set the main window icon from the embedded `icon.png` bytes (`include_bytes` + `Image::from_bytes` + `WebviewWindow::set_icon`), requiring the Tauri `image-png` feature

#### Scenario: Window chrome brand after rebuild
- **WHEN** the user launches a desktop shell binary built after the current icon assets were generated
- **THEN** the main window icon shows the product brand mark
- **AND** the mark matches the mark in `src/app/favicon.ico` (same visual source).

#### Scenario: Icons regenerated without rebuild
- **WHEN** maintainers only overwrite `src-tauri/icons/*` and run an older pre-existing exe
- **THEN** the old window/taskbar icon may still appear
- **AND** the documented fix is to regenerate icons (if needed) and rebuild the shell, not to expect live reload of icon resources.
