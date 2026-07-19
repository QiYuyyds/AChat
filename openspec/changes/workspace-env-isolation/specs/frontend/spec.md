## ADDED Requirements

### Requirement: Frontend SHALL render workspace env hint card

When the frontend receives a `workspace_env_hint` SSE event, it MUST render a workspace env hint card that offers the user three actions: "Create .venv", "Skip", and "Use system Python". The card MUST be displayed as a workspace-level banner (not inline in the chat message stream) because env setup is a workspace-level concern that should be resolved before the agent starts working. The card MUST be dismissible but reappear if the user has not made a choice and a new hint event arrives.

#### Scenario: User sees the hint card after creating a Python workspace

- **WHEN** the SSE stream delivers a `workspace_env_hint` event for the current conversation
- **THEN** the frontend renders a banner card with the three action buttons
- **AND** the card explains that creating a `.venv` isolates Python dependencies from the AChat runtime.

#### Scenario: User clicks Create .venv

- **WHEN** the user clicks "Create .venv"
- **THEN** the frontend calls `POST /api/workspaces/{conversation_id}/create-venv`
- **AND** the card transitions to a "Creating..." state on `workspace_env_status` with `status='creating'`
- **AND** on `status='ready'` the card shows a success message and dismisses
- **AND** on `status='failed'` the card shows the error and offers a retry button.

#### Scenario: User clicks Skip

- **WHEN** the user clicks "Skip"
- **THEN** the frontend calls `PATCH /api/workspaces/{conversation_id}/env-preference` with `preference='skip'`
- **AND** the card dismisses.

#### Scenario: User clicks Use system Python

- **WHEN** the user clicks "Use system Python"
- **THEN** the frontend calls `PATCH /api/workspaces/{conversation_id}/env-preference` with `preference='system_python'`
- **AND** the card dismisses.

### Requirement: Frontend SSE reducer SHALL handle workspace env events

The Zustand SSE reducer MUST handle `workspace_env_hint` and `workspace_env_status` events. The reducer MUST store the hint state keyed by `conversationId` so the UI can render or dismiss the card independently per conversation. The reducer MUST apply events idempotently (receiving the same hint event twice does not create duplicate cards).

#### Scenario: Reducer applies hint event

- **WHEN** a `workspace_env_hint` event arrives
- **THEN** the store sets `workspaceEnvHints[conversationId]` to the hint payload
- **AND** the UI renders the card.

#### Scenario: Reducer applies status event

- **WHEN** a `workspace_env_status` event with `status='ready'` arrives
- **THEN** the store clears `workspaceEnvHints[conversationId]`
- **AND** the card is removed from the UI.

#### Scenario: Reducer deduplicates hint events

- **WHEN** the same `workspace_env_hint` event is delivered twice (e.g., SSE reconnect)
- **THEN** the store does not create a duplicate entry
- **AND** only one card is rendered.
