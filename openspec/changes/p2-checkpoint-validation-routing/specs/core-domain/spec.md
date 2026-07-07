# Spec Delta: Core Domain

## MODIFIED Requirements

### Requirement: Agent runs SHALL be auditable

Each agent execution MUST create an AgentRun record with trigger message, parent run if any, status, timestamps, and usage when reported. AgentRun records MAY have associated checkpoints when checkpoint is enabled, and SHALL support resume from the latest checkpoint for SDK adapter runs.

#### Scenario: Adapter throws

- **WHEN** an adapter stream fails
- **THEN** the AgentRun status becomes `failed`
- **AND** the user sees an error message in the conversation.

#### Scenario: SDK run with checkpoint enabled

- **WHEN** an SDK agent run has `checkpoint_enabled=True`
- **THEN** checkpoints are saved during the run
- **AND** the run can be resumed from its latest checkpoint after failure or cancellation.

#### Scenario: Resume creates a new run referencing the original

- **WHEN** a run is resumed from checkpoint
- **THEN** the resumed run reuses the original `run_id`
- **AND** the `AgentRun` status is updated from `failed`/`aborted` back to `running`
- **AND** the `started_at` timestamp is preserved (original start time)
- **AND** the `finished_at` timestamp is cleared.
