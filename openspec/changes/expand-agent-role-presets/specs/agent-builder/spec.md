## MODIFIED Requirements

### Requirement: Custom agents SHALL provide tool presets

The agent builder MUST provide one-click tool presets for common custom-agent roles. Each preset MUST bind a differentiated system prompt template (not just a tool list). The preset catalog MUST cover nine roles: all-purpose, local-code, artifact, review, tech-writing, testing-qa, frontend-design, researcher, and data-analysis. Presets apply only to Custom adapter agents; SDK agents (Claude Code, Codex) use their own built-in tool sets and are unaffected.

Each preset MUST define `{ id, label, desc, tools, systemPromptTemplate }`. The `systemPromptTemplate` is a static, deterministic text based on a shared six-principle scaffold where principles 1 (context acquisition), 4 (output strategy), and 5 (file/command operation) are tailored to the role's core tools.

#### Scenario: User selects tech-writing preset

- **WHEN** the user clicks the tech-writing tool preset
- **THEN** the selected tools include `write_artifact`, `read_artifact`, `read_attachment`, `ask_user`, `fs_read`, `fs_list`, `fs_glob`, and `fs_grep`
- **AND** the System Prompt is overwritten with the tech-writing template
- **AND** code-modifying tools (`fs_write`, `fs_edit`, `bash`) are not selected unless the user adds them manually.

#### Scenario: User selects testing-qa preset

- **WHEN** the user clicks the testing-qa tool preset
- **THEN** the selected tools include `bash`, `fs_read`, `fs_list`, `fs_glob`, `fs_grep`, `fs_write`, `read_artifact`, `ask_user`, and `write_artifact`
- **AND** `fs_edit` is NOT selected (QA writes test files but does not modify business code)
- **AND** the System Prompt is overwritten with the testing-qa template.

#### Scenario: User selects researcher preset

- **WHEN** the user clicks the researcher tool preset
- **THEN** the selected tools include `web_search`, `ask_user`, `read_attachment`, `write_artifact`, and `read_artifact`
- **AND** no `fs_*` or `bash` tools are selected
- **AND** the System Prompt is overwritten with the researcher template.

#### Scenario: User creates a custom agent

- **WHEN** the create dialog opens for a Custom adapter agent
- **THEN** the default preset is all-purpose
- **AND** both artifact tools and local workspace file/command tools are selected
- **AND** the System Prompt is prefilled with the all-purpose template.

### Requirement: Role selection SHALL auto-overwrite tools and system prompt

Selecting a role preset MUST immediately switch the tool checklist to the preset's tool set AND overwrite the System Prompt field with the preset's `systemPromptTemplate`. The user may manually adjust tools and prompt after the overwrite.

#### Scenario: User switches from all-purpose to review

- **WHEN** the user clicks the review preset while the System Prompt contains the all-purpose template
- **THEN** the tool checklist switches to the review tool set (`read_artifact`, `read_attachment`, `ask_user`, `fs_list`, `fs_read`, `bash`)
- **AND** the System Prompt is overwritten with the review template.

#### Scenario: Editing an existing agent

- **WHEN** the edit dialog opens for an existing Custom agent with a persisted `systemPrompt`
- **THEN** the initial active preset is inferred from the agent's persisted `toolNames`
- **AND** the persisted `systemPrompt` is NOT overwritten (only manual preset clicks overwrite).

### Requirement: New custom agents SHALL start with a role-specific harness prompt

The create dialog MUST prefill `systemPrompt` with the default preset's (all-purpose) template. This replaces the previous generic scaffold with a role-aware prompt that explains goal handling, context loading, tool use, artifact output, workspace safety, and final response expectations tailored to the selected role.

#### Scenario: User opens create dialog

- **WHEN** no existing agent is being edited
- **THEN** the System Prompt field contains the all-purpose template
- **AND** switching to another role overwrites it with that role's template.
