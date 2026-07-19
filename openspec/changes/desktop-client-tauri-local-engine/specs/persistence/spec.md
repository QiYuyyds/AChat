## ADDED Requirements

### Requirement: Online desktop mode SHALL treat cloud PostgreSQL as authoritative via API

When the official cloud API is reachable, conversation, message, agent configuration, and user settings durability MUST be committed through official HTTP APIs backed by cloud PostgreSQL. Desktop mode MUST NOT open a direct SQL connection to cloud PostgreSQL for normal operation.

#### Scenario: Online message persistence
- **WHEN** a desktop Agent run produces a message that must be stored while online
- **THEN** persistence is performed through the official cloud API
- **AND** the cloud database remains the authority for multi-device online reads.

### Requirement: Desktop mode SHALL provide local SQLite offline store

Desktop mode MUST maintain a local SQLite database under the desktop data directory for offline continuation and upload outbox/cache needs. This store is subordinate to cloud authority when online reconciliation runs.

#### Scenario: Offline write
- **WHEN** cloud APIs are unreachable and the user continues core chat on desktop
- **THEN** new local activity is recorded in SQLite
- **AND** remains available on that machine until sync is attempted.

### Requirement: Offline sync SHALL be best-effort with visible conflicts in v1

On reconnect, the local engine MUST attempt to upload offline-produced changes. v1 MUST NOT claim conflict-free multi-device offline merge. Conflicts or rejected uploads MUST be visible to the user or logs/UI status.

#### Scenario: Sync after offline period
- **WHEN** connectivity returns after offline writes
- **THEN** the engine attempts upload of pending outbox items
- **AND** successful items become visible via cloud-backed history
- **AND** failed/conflicting items are not silently dropped without notice.

### Requirement: Workspace file blobs SHALL remain local for desktop local bindings

Bound local project directories and sandbox workspace files used by desktop execution MUST remain on the user filesystem. Cloud persistence of full workspace trees is not a v1 requirement.

#### Scenario: File edit tool on desktop
- **WHEN** an agent edits a file in a local bound workspace
- **THEN** the change is written on the local disk path
- **AND** cloud storage of that file content is not required for the edit to succeed.
