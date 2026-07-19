## ADDED Requirements

### Requirement: Windows installer SHALL bundle shell and local engine runtime

The v1 distribution artifact MUST be a Windows installer or packaged app that includes the Tauri shell and the local engine runtime dependencies required to start without a pre-installed Python from the user. It MUST NOT require the user to clone the git repository.

#### Scenario: Clean machine install
- **WHEN** a user without the AChat repo installs the desktop package on Windows
- **THEN** they can launch the app and reach the official login UI after engine start
- **AND** they are not instructed to install Python as a hard prerequisite for basic launch.

### Requirement: Package SHALL embed official endpoint configuration

The package MUST contain fixed official frontend and API base URLs (or build-flavor equivalents) used by the shell and local engine. v1 MUST NOT require the end user to enter infrastructure connection strings for PostgreSQL/Milvus.

#### Scenario: First launch configuration burden
- **WHEN** a new user finishes installation and launches the app
- **THEN** they are not forced through a wizard to paste Postgres/Milvus URLs for default official use
- **AND** the app targets the packaged official endpoints.

### Requirement: Distribution channel SHALL support first manual download and later in-app whole-package update

Users MUST be able to obtain the first installer via a published download location. After installation, the app MUST be able to check for whole-package updates from a configured feed.

#### Scenario: First install
- **WHEN** a user downloads the installer from the published channel
- **THEN** installation yields a runnable desktop app entrypoint.

#### Scenario: Later update
- **WHEN** a newer whole package is published to the update feed
- **THEN** the installed app can discover and apply that package update in v1.

### Requirement: v1 distribution MAY ship without code signing

v1 MUST document that Windows SmartScreen or OS warnings may appear for unsigned binaries. Lack of signing MUST NOT block engineering release of an internal/public unsigned v1 if product accepts that trade-off.

#### Scenario: Unsigned binary launch
- **WHEN** a user runs the v1 unsigned installer or executable
- **THEN** product documentation explains how to proceed past OS warnings
- **AND** the application can still run if the user approves.

### Requirement: Installer SHALL NOT bundle Claude or Codex CLIs

Desktop packages MUST NOT include Anthropic Claude Code CLI or OpenAI Codex CLI binaries as bundled runtimes in v1. Optional detection and install guidance are allowed.

#### Scenario: Package contents inspection
- **WHEN** the release package is built
- **THEN** it does not ship claude/codex CLI product binaries as a required bundled component.

### Requirement: Desktop data directory SHALL live under per-user application data

Local engine state (logs, SQLite offline store, runtime files) MUST default under the Windows per-user application data location for AChat, not under Program Files.

#### Scenario: Engine writes offline cache
- **WHEN** the local engine creates SQLite offline data
- **THEN** files are stored under the configured per-user AChat data directory.

### Requirement: Installer and shell icons SHALL match the official product brand mark

The Windows package icons (installer, start-menu/shortcut, application window) MUST be derived from `src/app/favicon.ico` (or its in-repo successor) via the desktop icon pipeline. Scaffold/placeholder icons MUST NOT ship. Agent avatars under `public/agent-icons/` are out of scope. Web browser favicon/login-page styling is **not** an acceptance requirement of this desktop change.

Regeneration pipeline (documented for maintainers):

1. Update `src/app/favicon.ico` if the mark changes
2. Run `python apps/desktop/scripts/generate-icons.py` to refresh `apps/desktop/src-tauri/icons/*`
3. Rebuild/repackage the desktop shell (`pnpm build` / Tauri bundler) so NSIS/exe resources embed the new icons
4. For installed users, distribute a new package; Windows may cache old shortcut icons until reinstall or cache refresh

#### Scenario: Fresh install from a package built with current icons
- **WHEN** a user installs a desktop package whose build used the current generated icons
- **THEN** the shortcut/taskbar/window icon is recognizably the product brand mark from `src/app/favicon.ico`
- **AND** the package does not rely on empty or corrupt scaffold `icons/*` placeholders.

#### Scenario: Regenerating icons for a new release
- **WHEN** maintainers update the source brand file and run the generate-icons script, then rebuild the installer
- **THEN** the new package embeds the updated icons without inventing a second unrelated logo
- **AND** shipping only updated files under `icons/` without a new package build is not considered a complete release step.
