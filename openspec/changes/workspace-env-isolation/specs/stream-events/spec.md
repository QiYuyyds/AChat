## ADDED Requirements

### Requirement: Workspace env hint events SHALL drive venv creation UI

When a workspace environment detection finds a Python project without a `.venv`, AChat MUST publish a `workspace_env_hint` event through the EventBus so the frontend can render a venv creation prompt. When the user triggers venv creation or the detection status changes, AChat MUST publish `workspace_env_status` events with progress and result. Both events MUST carry the owning `user_id` and `conversationId` so the EventBus can filter delivery to the correct SSE subscribers.

#### Scenario: Python project without venv triggers hint

- **WHEN** `WorkspaceEnvService.detect_project_env()` finds a Python project with no `.venv` and `Workspace.env_preference` is `null`
- **THEN** AChat publishes a `workspace_env_hint` event with `conversationId`, `language='python'`, `venvPresent=false`, and `options=['create', 'skip', 'system_python']`
- **AND** the EventBus delivers it to the owning user's SSE subscribers.

#### Scenario: User initiates venv creation

- **WHEN** the frontend calls `POST /api/workspaces/{conversation_id}/create-venv`
- **THEN** AChat publishes `workspace_env_status` with `status='creating'`
- **AND** on success publishes `workspace_env_status` with `status='ready'` and the venv path
- **AND** on failure publishes `workspace_env_status` with `status='failed'` and the error message.

#### Scenario: Non-Python project emits no hint

- **WHEN** the detected project language is `nodejs`, `java`, or `go`
- **THEN** no `workspace_env_hint` event is published.

#### Scenario: Already-resolved workspace emits no hint

- **WHEN** `Workspace.env_preference` is `'venv_created'`, `'skip'`, or `'system_python'`
- **THEN** no `workspace_env_hint` event is published even if the project is Python without a venv.
