## MODIFIED Requirements

### Requirement: Agent builder SHALL expose adapter-specific fields

Create/edit agent UI MUST show provider, model, tool, key, and base URL fields according to selected adapter semantics. For Custom adapter agents, the tool checklist MUST only show the 5 UI-selectable tools (`write_artifact`, `deploy_artifact`, `deploy_workspace`, `read_artifact`, `web_search`). The 9 baseline tools (`read_attachment`, `ask_user`, `fs_list`, `fs_read`, `fs_write`, `fs_edit`, `fs_grep`, `fs_glob`, `bash`) MUST NOT appear as checkboxes—they are implicitly always-on for Custom agents and the UI MUST display a non-interactive hint stating "所有 custom agent 自带以下基础工具" followed by the baseline tool list.

#### Scenario: User selects Codex adapter

- **WHEN** `adapterKind='codex'`
- **THEN** provider and AChat tool checkboxes are hidden
- **AND** Base URL copy says it must support Codex/Responses.

#### Scenario: Custom adapter tool checklist

- **WHEN** `adapterKind='custom'` and the tools tab is open
- **THEN** the tool checklist shows exactly 5 checkboxes: write_artifact, deploy_artifact, deploy_workspace, read_artifact, web_search
- **AND** a non-interactive hint section displays "所有 custom agent 自带以下基础工具" with the 9 baseline tool names and their descriptions
- **AND** baseline tools cannot be toggled off.

### Requirement: Role selection SHALL auto-overwrite tools and system prompt

The create/edit agent dialog MUST display exactly 4 role preset buttons: coder, researcher, orchestrator, writer. Selecting a preset MUST immediately switch the 5-checkbox tool checklist to the preset's tool set AND overwrite the System Prompt field with the preset's `systemPromptTemplate`. The user may manually adjust the 5 checkboxes and the prompt after the overwrite.

#### Scenario: User switches from coder to writer

- **WHEN** the user clicks the writer preset while the System Prompt contains the coder template
- **THEN** the 5-checkbox tool checklist switches to writer's set (write_artifact ✓, deploy_artifact ✓, deploy_workspace ·, read_artifact ✓, web_search ·)
- **AND** the System Prompt is overwritten with the writer template.

#### Scenario: Editing an existing agent infers preset

- **WHEN** the edit dialog opens for an existing Custom agent with persisted `toolNames`
- **THEN** the initial active preset is inferred by matching the agent's non-baseline `toolNames` against the 4 presets' tool sets
- **AND** if no exact match is found, no preset is highlighted (user's custom configuration)
- **AND** the persisted `systemPrompt` is NOT overwritten.
