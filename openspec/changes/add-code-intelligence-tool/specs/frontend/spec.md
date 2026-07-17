## ADDED Requirements

### Requirement: Local conversation creation SHALL offer source intelligence

The create dialog SHALL show a default-OFF “启用源码智能” switch below the bound-local-path warning and hide it for sandbox. Creation MUST not wait for indexing.

### Requirement: Chat header SHALL expose index status

The Workspace toolbar SHALL show a source-graph control beside the folder control with disabled, building, ready, stale, failed and interrupted states.

### Requirement: The status panel SHALL use a sliding switch as primary control

Clicking the header control SHALL open a panel whose first row contains “源码智能” and an ON/OFF sliding switch. Project, runtime, phase, counts, last sync, error and valid actions SHALL appear below.

#### Scenario: Switch turns ON
- **WHEN** disabled user moves it ON
- **THEN** UI confirms runtime preparation and `.codegraph` creation, then calls enable and shows progress.
- **AND** cancelled confirmation or failed enable restores OFF.

#### Scenario: Enabled state is not ready
- **WHEN** preparing, queued, indexing, syncing, rebuilding, failed or interrupted
- **THEN** switch remains ON to represent enabled intent and details show appropriate progress/error/action.

#### Scenario: Switch turns OFF
- **WHEN** user moves ON to OFF
- **THEN** UI confirms stopping work/tool access while preserving `.codegraph`, then disables.
- **AND** cancelled confirmation or failed disable restores ON.

#### Scenario: Transition request is pending
- **WHEN** enable/disable is in flight
- **THEN** switch is temporarily disabled.

### Requirement: Progress SHALL use isolated REST polling

Frontend MUST use dedicated REST endpoints, poll quickly only while panel open or task non-terminal, stop high-frequency polling at ready/disabled, refresh on focus and clean timers on conversation change/unmount. Completion/failure MAY show one toast but MUST NOT enter chat history.

#### Scenario: Active progress is visible
- **WHEN** status contains a whole-run progress percentage for active index or rebuild work
- **THEN** the panel shows one determinate bar and the exact percentage without decorative pulse animation.

#### Scenario: Indexing completes
- **WHEN** status becomes `ready`
- **THEN** the progress region disappears and the existing green ready summary, statistics and actions are shown.

#### Scenario: Source-intelligence switch changes state
- **WHEN** either the create-dialog switch or panel switch is OFF or ON
- **THEN** its thumb remains fully inside the switch track and surrounding card boundary.
