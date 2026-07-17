## ADDED Requirements

### Requirement: AChat SHALL manage a pinned CodeGraph runtime

AChat MUST provide a fixed-version runtime without requiring user installation. Artifacts MUST come from a manifest with platform, architecture, fixed HTTPS URL, SHA256 and license metadata; packaged runtime is preferred, then verified cache, then user-approved download.

#### Scenario: Runtime download is required
- **WHEN** the user enables source intelligence and no packaged/cached runtime exists
- **THEN** AChat downloads to temporary storage, verifies SHA256, safely extracts and atomically installs it.

#### Scenario: Verification fails
- **WHEN** digest or extraction validation fails
- **THEN** partial files are removed, nothing executes, and only that Workspace becomes failed.

### Requirement: Source intelligence SHALL be explicit and local-only

The capability MUST default disabled and only support local Workspace. Enabling is a Workspace decision, not an Agent setting.

#### Scenario: User enables during creation
- **WHEN** a local conversation is created with source intelligence enabled
- **THEN** the conversation opens immediately and indexing starts in background.

#### Scenario: User enables from existing conversation
- **WHEN** the top-panel switch is turned ON and confirmed
- **THEN** AChat persists enabled intent and starts preparation/indexing without blocking chat.

#### Scenario: Feature is disabled or sandboxed
- **WHEN** the user leaves it OFF or uses sandbox
- **THEN** no download, process, index write, polling or tool injection occurs.

### Requirement: Index lifecycle SHALL be observable and controllable

Status MUST include enabled intent, runtime version, lifecycle state, phase, bounded counts, timestamps and error. Operations MUST include cancel, retry, sync, rebuild and disable.

#### Scenario: Index is building
- **WHEN** an index task is active
- **THEN** status reports progress and the user can cancel without blocking chat.

#### Scenario: Index progress advances
- **WHEN** CodeGraph reports live phase progress during init or rebuild
- **THEN** status exposes a bounded, monotonic whole-run percentage derived only from those real progress records.
- **AND** active progress remains below 100; only the `ready` lifecycle state represents completion.

#### Scenario: Application restarts mid-task
- **WHEN** metadata is non-terminal but no task exists
- **THEN** status becomes `interrupted` and permits retry.

#### Scenario: User disables
- **WHEN** disable is confirmed
- **THEN** active work stops, tool access stops, and `.codegraph` is preserved by default.

### Requirement: Index work SHALL be isolated

Indexing MUST be outside request/Agent critical paths, allow one task per project, use bounded global concurrency, and scope failures to one Workspace.

#### Scenario: Other conversation runs during indexing
- **WHEN** one Workspace indexes and another sends a message
- **THEN** the other Agent run proceeds normally.

#### Scenario: CodeGraph fails
- **WHEN** runtime, init, sync, rebuild or explore fails
- **THEN** chat, RAG, Memory, Artifact, SSE and other Workspaces remain functional.

### Requirement: Ready Workspaces SHALL provide bounded code exploration

`code_explore` MUST accept non-empty `query`, derive project path from Workspace, execute only when local/enabled/ready, and return bounded source/call-path/impact context.

#### Scenario: Ready project is explored
- **WHEN** an eligible Agent calls the tool
- **THEN** AChat checks freshness, syncs if required and returns bounded CodeGraph output.

#### Scenario: Index is unavailable
- **WHEN** disabled/building/failed/interrupted or sync fails
- **THEN** the tool returns a non-fatal error and directs the Agent to file search/read tools.

### Requirement: CodeGraph work SHALL be cancellable and cleaned up

Download, init, sync, rebuild and explore MUST have timeout/cancellation and complete process-tree cleanup on cancellation, Bridge teardown or app shutdown.
