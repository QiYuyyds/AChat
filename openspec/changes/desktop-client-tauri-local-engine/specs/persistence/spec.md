## ADDED Requirements

### Requirement: Online desktop mode SHALL treat remote PostgreSQL as the primary authority via direct engine connections

When the configured remote PostgreSQL is reachable, conversation, message, agent configuration, and user settings durability MUST be committed by the local engine through its normal DB access path to that PostgreSQL. Desktop mode MUST NOT require an official AChat business HTTP API process as the only write path.

#### Scenario: Online message persistence
- **WHEN** a desktop Agent run produces a message that must be stored while the primary database is reachable
- **THEN** persistence is performed by the local engine against the configured PostgreSQL
- **AND** that database remains the multi-device online authority for the same deployment.

### Requirement: Desktop mode SHALL provide local SQLite cache and offline store

Desktop mode MUST maintain a local SQLite database under the desktop data directory for offline continuation and sync outbox/cache needs. This store is subordinate to the primary PostgreSQL authority when online reconciliation runs.

#### Scenario: Offline write
- **WHEN** the primary database is unreachable and the user continues core chat on desktop
- **THEN** new local activity is recorded in SQLite
- **AND** remains available on that machine until sync is attempted.

### Requirement: Offline sync SHALL be best-effort with visible conflicts in v1

On reconnect, the local engine MUST attempt to sync offline-produced changes to the primary store. v1 MUST NOT claim conflict-free multi-device offline merge. Conflicts or rejected uploads MUST be visible to the user or logs/UI status.

#### Scenario: Sync after offline period
- **WHEN** connectivity returns after offline writes
- **THEN** the engine attempts sync of pending outbox items
- **AND** successful items become visible via primary-store-backed history
- **AND** failed/conflicting items are not silently dropped without notice.

### Requirement: Workspace file blobs SHALL remain local for desktop local bindings

Bound local project directories and sandbox workspace files used by desktop execution MUST remain on the user filesystem. Remote persistence of full workspace trees is not a v1 requirement.

#### Scenario: File edit tool on desktop
- **WHEN** an agent edits a file in a local bound workspace
- **THEN** the change is written on the local disk path
- **AND** remote storage of that file content is not required for the edit to succeed.

### Requirement: Infrastructure endpoints SHALL be configurable with packaged defaults

Default infrastructure endpoints for the official deployment MUST be embeddable in the package. Users MUST be able to override those endpoints to self-hosted infrastructure without rebuilding the app.

#### Scenario: Default package uses official infra
- **WHEN** no user override exists
- **THEN** the engine uses packaged default infrastructure configuration.

#### Scenario: Override takes effect
- **WHEN** a user saves a valid override configuration
- **THEN** the engine uses the override for subsequent connections
- **AND** secrets from the override are not written to application logs.
