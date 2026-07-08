# Spec Delta: Run Checkpoint

## ADDED Requirements

### Requirement: Agent runs SHALL support turn-level checkpoint saving

SDK agent runs MUST save a checkpoint after each turn when checkpoint is enabled. A checkpoint MUST capture the full `messages` list, turn number, and run metadata. Checkpoints MUST be stored in the `agent_run_checkpoints` table.

#### Scenario: Checkpoint saved after each turn

- **WHEN** an SDK agent run with `checkpoint_enabled=True` completes turn N
- **THEN** a checkpoint is saved with `run_id`, `turn_number=N`, `messages` (the full conversation history at that point)
- **AND** the checkpoint is persisted to the `agent_run_checkpoints` table.

#### Scenario: Checkpoint disabled by default

- **WHEN** an agent run does not have `checkpoint_enabled` set
- **THEN** no checkpoints are saved
- **AND** the run proceeds without checkpoint overhead.

#### Scenario: CLI agent run does not checkpoint

- **WHEN** a CLI adapter (Claude Code / Codex) agent run completes a turn
- **THEN** no checkpoint is saved (the turn loop is inside the CLI subprocess).

### Requirement: Checkpoint retention SHALL be bounded per run

Each run MUST retain at most 3 checkpoints (the latest plus 2 historical). When saving a new checkpoint exceeds the limit, the oldest checkpoint for that run MUST be deleted.

#### Scenario: Fourth checkpoint evicts the oldest

- **WHEN** a run already has 3 checkpoints
- **AND** a new checkpoint is saved for turn N+1
- **THEN** the oldest checkpoint (lowest turn_number) is deleted
- **AND** the new checkpoint is persisted.

#### Scenario: Checkpoints cleaned on run completion

- **WHEN** a run completes (success or failure)
- **THEN** only the latest checkpoint is retained
- **AND** historical checkpoints are deleted.

### Requirement: Agent runs SHALL support resume from checkpoint

AgentRunner MUST support resuming an SDK agent run from its latest checkpoint. Resume MUST reconstruct the `messages` list from the checkpoint and continue the ReAct loop from the next turn.

#### Scenario: Resume from latest checkpoint

- **WHEN** `POST /api/runs/{run_id}/resume` is called for a run with checkpoints
- **THEN** AgentRunner loads the latest checkpoint
- **AND** reconstructs `messages` from the checkpoint
- **AND** continues the ReAct loop from `turn_number + 1`
- **AND** the resumed run uses the same `agent_id`, `conversation_id`, and `run_id`.

#### Scenario: Resume with no checkpoint

- **WHEN** resume is called for a run with no checkpoints
- **THEN** the API returns a 404 error with a clear message
- **AND** no new run is started.

#### Scenario: Resume a completed run

- **WHEN** resume is called for a run with status `complete`
- **THEN** the API returns a 409 conflict error
- **AND** no new run is started.

### Requirement: Checkpoint API SHALL expose available checkpoints

The system MUST provide `GET /api/runs/{run_id}/checkpoints` to list available checkpoints for a run, returning checkpoint ids, turn numbers, and timestamps.

#### Scenario: List checkpoints for a run

- **WHEN** `GET /api/runs/{run_id}/checkpoints` is called
- **THEN** the response contains an array of checkpoints ordered by turn_number descending
- **AND** each checkpoint entry includes `id`, `turn_number`, `created_at`.
