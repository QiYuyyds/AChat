## ADDED Requirements

### Requirement: Managed runtime SHALL be pinned and verified

Artifacts MUST use fixed version/platform/architecture/HTTPS URL/SHA256/license manifest. Download MUST use temporary storage, verify before execution, reject archive traversal and install atomically.

#### Scenario: Digest mismatch or unsafe archive
- **WHEN** verification fails
- **THEN** partial data is deleted, nothing executes, and failure remains Workspace-scoped.

### Requirement: Subprocess invocation SHALL prevent injection

Only packaged or verified executables and fixed subcommands MAY run. cwd/project path MUST come from Workspace and query MUST be argv data. `shell=True`, model-controlled executable/cwd/env/path and concatenated shell commands are forbidden.

#### Scenario: Query contains metacharacters
- **WHEN** query contains quotes, newline, Unicode or `& | < > ^ % !`
- **THEN** it remains one data argument, no second command runs, and cwd is unchanged.

### Requirement: Process lifecycle SHALL be bounded

Download/init/sync/rebuild/explore MUST support timeout/cancellation. Complete process trees and partial runtime data MUST be cleaned on cancellation, Bridge teardown or app shutdown.
