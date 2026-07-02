## ADDED Requirements

### Requirement: Agent builder tool-prompt layout SHALL use a horizontal role bar with split panes

The create/edit agent dialog's "tools & prompt" tab MUST render a horizontal role bar of preset capsules using `flex flex-wrap` (wrapping to multiple rows, NOT horizontal scrolling) above a left-right split pane: the left pane shows the tool checklist in a multi-column grid, the right pane shows the System Prompt editor. This replaces the previous vertical stack (2-column preset grid + single-column tool list + prompt at bottom).

The role bar MUST show all nine role capsules simultaneously. The active preset MUST be visually highlighted. Selecting a capsule MUST switch the left tool checklist and overwrite the right prompt editor in one action (see agent-builder spec for the auto-overwrite requirement).

#### Scenario: User opens the tools & prompt tab for a Custom agent

- **WHEN** the user navigates to the tools & prompt tab with adapter kind Custom
- **THEN** a horizontal role bar renders all nine role capsules wrapped across rows
- **AND** the active preset capsule is highlighted
- **AND** the left pane shows the tool checklist in a multi-column grid
- **AND** the right pane shows the System Prompt editor.

#### Scenario: Nine roles on a narrow viewport

- **WHEN** the dialog width is too narrow to fit all nine capsules in one row
- **THEN** the capsules wrap to additional rows without horizontal scrolling
- **AND** all nine remain visible and clickable.

#### Scenario: SDK adapter hides the role bar

- **WHEN** the adapter kind is Claude Code or Codex
- **THEN** the role bar and tool checklist are hidden
- **AND** a read-only notice explains the SDK built-in tool set
- **AND** the System Prompt editor remains visible.
