## MODIFIED Requirements

### Requirement: Custom agents SHALL provide 4 role presets

The agent builder MUST provide one-click tool presets for 4 custom-agent roles: coder, researcher, orchestrator, and writer. Each preset binds a subset of the 6 UI-selectable tools (`write_artifact`, `deploy_artifact`, `deploy_workspace`, `read_artifact`, `web_search`, `rag_search`) and a `systemPromptTemplate` covering role positioning, production strategy, behavior constraints, and quality standards.

#### Scenario: User selects coder preset

- **WHEN** the user clicks the coder tool preset
- **THEN** the selected tools include `deploy_workspace` and `read_artifact`
- **AND** `write_artifact` is not selected unless the user adds it manually.

#### Scenario: User selects researcher preset

- **WHEN** the user clicks the researcher tool preset
- **THEN** the selected tools include `write_artifact`, `read_artifact`, `web_search`, and `rag_search`.

#### Scenario: User creates a custom agent

- **WHEN** the create dialog opens for a Custom adapter agent
- **THEN** the default preset is coder
- **AND** `deploy_workspace` and `read_artifact` are selected.
