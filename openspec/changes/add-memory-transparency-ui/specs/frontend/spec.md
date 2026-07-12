## MODIFIED Requirements

### Requirement: Settings panel SHALL include a memory management tab
The settings panel SHALL include a "记忆管理" (Memory Management) tab with three sub-panels: long-term memories, user preferences, and session summaries.

#### Scenario: User opens memory management
- **WHEN** the user navigates to Settings → Memory Management
- **THEN** three sub-tabs are visible: "长期记忆", "用户偏好", "会话摘要"

### Requirement: Long-term memory panel SHALL support filtering and CRUD
The long-term memory sub-panel SHALL display memories in a table with columns: content, category, importance, tags, agent, created_at. It SHALL support filtering by agent_id, category, and text search. It SHALL support inline editing of content/importance/tags and deletion with confirmation.

#### Scenario: Filter memories by agent
- **WHEN** the user selects an Agent from the filter dropdown
- **THEN** only memories belonging to that agent are displayed

#### Scenario: Edit a memory
- **WHEN** the user clicks "编辑" on a memory row
- **THEN** an inline edit form appears with content, importance, category, and tags fields
- **AND** saving sends a PUT request to update the memory

#### Scenario: Delete a memory with confirmation
- **WHEN** the user clicks "删除" on a memory row
- **THEN** a confirmation dialog appears: "确定删除这条记忆？此操作不可撤销"
- **AND** confirming sends a DELETE request

### Requirement: User preference panel SHALL support editing and deletion
The user preference sub-panel SHALL display preferences as a key-value list. It SHALL support inline editing of values and deletion with confirmation.

#### Scenario: Edit preference value
- **WHEN** the user edits a preference value inline
- **THEN** saving sends a PUT request to update the preference

#### Scenario: Delete preference with confirmation
- **WHEN** the user clicks "删除" on a preference row
- **THEN** a confirmation dialog appears
- **AND** confirming sends a DELETE request

### Requirement: Session memory panel SHALL be read-only
The session memory sub-panel SHALL display session summaries grouped by conversation. It SHALL be read-only — no edit or delete actions.

#### Scenario: View session memory
- **WHEN** the user clicks on a conversation in the session memory panel
- **THEN** the Session Memory text is displayed in a read-only view
