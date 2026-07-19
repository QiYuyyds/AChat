## ADDED Requirements

### Requirement: Authoritative desktop delivery SHALL follow Tauri local-engine architecture

AChat's authoritative desktop delivery path MUST be: Tauri shell + local engine + official remote frontend/API. New desktop work MUST target that architecture.

#### Scenario: Planning a desktop feature
- **WHEN** a change adds desktop packaging or desktop-only runtime behavior
- **THEN** it aligns with Tauri shell and local-engine responsibilities
- **AND** does not reintroduce Electron+embedded Next as the required default.

## MODIFIED Requirements

### Requirement: Desktop build SHALL use Next standalone output

Desktop packaging MUST NOT require embedding a Next.js standalone server inside the desktop shell as the primary UI host. The official frontend MUST be loaded from the deployed official web URL. Local development of the web app MAY continue to use Next tooling independently of the desktop package.

#### Scenario: Packaged app starts
- **WHEN** the desktop application launches in production package mode
- **THEN** the shell loads the configured official frontend URL
- **AND** a local `.next/standalone` server is not required to be running for the primary UI.

### Requirement: Native and SDK packages SHALL remain external

Server-side native bindings and CLI-related runtime dependencies that the local engine needs MUST remain available to the local engine runtime packaging story. This requirement applies to the local engine bundle, not to an Electron-hosted Next server bundle.

#### Scenario: Engine packages runtime deps
- **WHEN** the local engine desktop artifact is built
- **THEN** required non-frontend runtime dependencies for adapters/tools are included or documented as external machine prerequisites (for CLI tools).

### Requirement: Desktop data SHALL live in userData

Desktop mode MUST store local runtime data (logs, SQLite offline store, local caches) under the per-user application data directory provided to the local engine, rather than the source checkout.

#### Scenario: Desktop app starts in production
- **WHEN** the local engine bootstraps in desktop mode
- **THEN** its data directory points at the desktop per-user application data path
- **AND** offline SQLite and logs are written there.

### Requirement: API key storage SHALL match web local mode

Desktop mode MUST use the same account-scoped cloud settings for durable API key storage as the web multi-user product. Keys are stored with the user account on the official server and retrieved by the local engine when online. Local SQLite MAY cache secrets only as an offline implementation detail with no weaker transport than HTTPS for cloud fetch. Desktop mode SHALL not require OS keychain storage in v1.

#### Scenario: User saves an OpenAI-compatible key on desktop
- **WHEN** settings are saved from the desktop UI
- **THEN** the key is stored through the official authenticated settings API for that user
- **AND** the local engine can resolve it for direct vendor calls when online.

### Requirement: Desktop packaging SHALL include Codex runtime dependencies

Desktop packaging MUST NOT treat bundling the Codex CLI product binary as a v1 requirement. If a user installs Codex CLI themselves, the local engine MUST be able to invoke it when present on PATH. Packaging MAY include notes or detection helpers but MUST NOT fail package build solely because Codex CLI bits are absent.

#### Scenario: User runs Codex agent from packaged app with CLI installed
- **WHEN** Codex CLI is installed on the machine and a Codex agent run starts
- **THEN** the local engine can locate and invoke the CLI from the environment.

#### Scenario: Codex CLI is not installed
- **WHEN** the user starts a Codex agent without a local CLI
- **THEN** the app returns a clear installation guidance error
- **AND** other agents remain usable.

## REMOVED Requirements

### Requirement: Desktop packaging SHALL include Electron Next dual-process bootstrap as the only path

**Reason**: Superseded by Tauri shell + local engine + remote official frontend.

**Migration**: Implement `desktop-shell`, `desktop-local-engine`, `desktop-bridge`, and `desktop-distribution` in change `desktop-client-tauri-local-engine`. Treat `desktop-electron-python` change as superseded.
