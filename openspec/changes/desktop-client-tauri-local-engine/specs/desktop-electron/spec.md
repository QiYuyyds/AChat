## ADDED Requirements

### Requirement: Authoritative desktop delivery SHALL follow Tauri local full-stack architecture

AChat's authoritative desktop delivery path MUST be: Tauri shell + local engine (full business backend) + packaged local static frontend + remote infrastructure (default official, user-overridable). New desktop work MUST target that architecture.

#### Scenario: Planning a desktop feature
- **WHEN** a change adds desktop packaging or desktop-only runtime behavior
- **THEN** it aligns with Tauri shell, local-engine, and local-frontend responsibilities
- **AND** does not reintroduce Electron+embedded Next as the required default
- **AND** does not reintroduce remote official business frontend as the required primary UI host.

## MODIFIED Requirements

### Requirement: Desktop UI SHALL be packaged local assets, not remote official web as primary host

Desktop packaging MUST embed frontend assets (or an equivalent offline-capable build) served from the local engine origin. Loading a remote official product website as the primary UI is not the v1 desktop product path. Local development of the web app MAY continue to use Next tooling independently of the desktop package.

#### Scenario: Packaged app starts
- **WHEN** the desktop application launches in production package mode
- **THEN** the shell loads the local UI origin after engine readiness
- **AND** a remote official business web deployment is not required for the primary UI to appear.

### Requirement: Native and SDK packages SHALL remain external to the shell

Server-side native bindings and CLI-related runtime dependencies that the local engine needs MUST remain available to the local engine runtime packaging story. This requirement applies to the local engine bundle, not to an Electron-hosted Next server bundle.

#### Scenario: Engine packages runtime deps
- **WHEN** the local engine desktop artifact is built
- **THEN** required non-frontend runtime dependencies for adapters/tools are included or documented as external machine prerequisites (for CLI tools).

### Requirement: Desktop data SHALL live in userData

Desktop mode MUST store local runtime data (logs, SQLite offline store, local caches, user infra overrides) under the per-user application data directory provided to the local engine, rather than the source checkout.

#### Scenario: Desktop app starts in production
- **WHEN** the local engine bootstraps in desktop mode
- **THEN** its data directory points at the desktop per-user application data path
- **AND** offline SQLite and logs are written there.

### Requirement: API key storage SHALL match web account storage on the primary store

API keys for desktop model calls MUST use the same account-backed settings model as web for the shared primary store, resolved by the local engine without a mandatory remote business-API proxy hop.
