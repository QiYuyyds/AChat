# Frontend

## ADDED Requirements

### Requirement: Frontend SHALL support dual-active-conversation model

AppState SHALL include `activeConversationId` (work conversation, unchanged) and `guideConversationId` (guide conversation, new). Both can be active simultaneously. SSE event reducers SHALL apply events to the correct conversation by `conversationId` bucketing. The guide floating panel SHALL read `guideConversationId` and SHALL NOT modify `activeConversationId`.

#### Scenario: User has both work and guide conversations active
- **WHEN** the user is in a work conversation and the guide panel is open
- **THEN** `activeConversationId` points to the work conversation
- **AND** `guideConversationId` points to the guide conversation
- **AND** SSE events for each conversation are applied to the correct message list.

#### Scenario: Guide conversation is auto-created on first login
- **WHEN** the user logs in and `guideConversationId` is null
- **THEN** the frontend creates a guide conversation (`mode='guide'`, `agentIds=['ag_guide_builtin']`)
- **AND** stores its id in `guideConversationId`
- **AND** opens the floating panel.

### Requirement: GuideFloatingPanel SHALL be a persistent floating component

The frontend SHALL render a `GuideFloatingPanel` component that floats above the main chat panel. The panel SHALL support drag (by header), resize (by corner handle), collapse/expand (by close button or `Ctrl/Cmd+G`), and position/size/open-state persistence to `localStorage` (per-user). The panel SHALL render a simplified message list (text/tool_use/ask_user parts only, no artifacts) and a simplified input (no attachments, no slash commands, no @mention). The panel's `z-index` SHALL be above the work chat panel but below modals/dialogs.

#### Scenario: Panel is dragged and resized
- **WHEN** the user drags the panel header to a new position and resizes via the corner handle
- **THEN** the new position and size are saved to `localStorage`
- **AND** restored on next page load.

#### Scenario: Panel is collapsed and expanded
- **WHEN** the user clicks the close button or presses `Ctrl/Cmd+G`
- **THEN** the panel collapses to a floating button (with unread indicator)
- **AND** clicking the floating button or pressing `Ctrl/Cmd+G` again expands it.

#### Scenario: ask_user renders inline in the panel
- **WHEN** the guide agent calls `ask_user`
- **THEN** the pending question renders inline in the panel's message list with option buttons
- **AND** the user's selection is sent via `POST /api/pending/questions/{id}/resolve`
- **AND** the option buttons are disabled after selection.

### Requirement: Frontend SHALL handle guide_side_effect events

The frontend SSE reducer SHALL handle `guide_side_effect` events by refreshing the corresponding panel. The `target` field determines which panel to refresh: `agents` → re-fetch agents list, `skills` → re-fetch skills, `mcp` → re-fetch MCP servers, `documents` → re-fetch documents, `memory` → re-fetch memories, `profile` → re-fetch profile/settings, `conversations` → re-fetch conversations list.

#### Scenario: guide_side_effect with target=agents
- **WHEN** the frontend receives a `guide_side_effect` event with `target='agents'`
- **THEN** it calls `fetchAgents()` to refresh the agents list
- **AND** the sidebar updates to show the new/updated/deleted agent.

#### Scenario: guide_side_effect with target=memory
- **WHEN** the frontend receives a `guide_side_effect` event with `target='memory'`
- **THEN** it refreshes the memory panel data
- **AND** the memory panel updates to reflect deletions/merges/updates.
