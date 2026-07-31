## ADDED Requirements

### Requirement: LTM panel SHALL display structured summary field

The long-term memory panel (`long-term-memory-panel.tsx`) SHALL display the `summary` field as a title line above the `content` text in each memory card. When `summary` is empty, the title line SHALL be hidden and the card SHALL display only `content` (backward compatible with unmigrated memories).

#### Scenario: Card with summary shows title

- **WHEN** a memory item has `summary="用户前端技术栈"` and `content="用户喜欢TypeScript，偏好React框架"`
- **THEN** the card SHALL display "用户前端技术栈" as a title line with `font-medium` styling
- **AND** the content SHALL appear below the title

#### Scenario: Card without summary hides title

- **WHEN** a memory item has `summary=""`
- **THEN** no title line SHALL be rendered
- **AND** the card SHALL display only the content (same as current behavior)

### Requirement: LTM panel SHALL display keywords as distinct tags

The panel SHALL display `keywords` with a visually distinct style from `tags` (e.g., `#` prefix or different color). This reflects the semantic difference: `tags` are structural classification labels, `keywords` are retrieval terms. When `keywords` is empty, no keyword tags SHALL be rendered.

#### Scenario: Keywords displayed with distinct style

- **WHEN** a memory item has `keywords=["TypeScript", "React"]` and `tags=["user"]`
- **THEN** keywords SHALL be rendered with a style visually distinct from tags
- **AND** both keyword tags and structural tags SHALL be visible in the card

#### Scenario: Empty keywords hidden

- **WHEN** a memory item has `keywords=[]`
- **THEN** no keyword tags SHALL be rendered

### Requirement: LTM panel SHALL display contentScope as path annotation

The panel SHALL display `contentScope` as a path annotation in the card's metadata row (alongside agentId and createdAt) when the field is non-empty. The path SHALL be rendered with a monospace font and a folder icon prefix. When `contentScope` is empty, it SHALL be hidden.

#### Scenario: Project-scoped memory shows path

- **WHEN** a memory item has `contentScope="d:/java/project/agenthub"`
- **THEN** the metadata row SHALL display the path with a folder icon and monospace font

#### Scenario: User-level memory hides path

- **WHEN** a memory item has `contentScope=""`
- **THEN** no path annotation SHALL be rendered

### Requirement: LTM panel edit form SHALL support structured fields

The edit form SHALL include input fields for `summary` (single-line text), `keywords` (comma-separated text, same pattern as tags), and `contentScope` (single-line text). The `startEdit` function SHALL populate these fields from the item, and `handleSave` SHALL submit them in the update request body.

#### Scenario: Edit form includes structured fields

- **WHEN** the user clicks edit on a memory item
- **THEN** the edit form SHALL show input fields for summary, keywords, and contentScope
- **AND** each field SHALL be pre-populated with the item's current value

#### Scenario: Save submits structured fields

- **WHEN** the user edits summary/keywords/contentScope and clicks save
- **THEN** the update request body SHALL include `summary`, `keywords` (as array), and `contentScope`
- **AND** keywords SHALL be parsed from comma-separated input to array (same as tags)

### Requirement: LTM panel category filter SHALL align with backend categories

The category filter dropdown SHALL list categories that the backend actually produces: empty string (通用), `fact` (事实), `preference` (偏好), `policy` (策略), `tool_failure` (工具失败), `identity` (身份), and `case` (任务经验). Categories that the backend never produces (`general`, `skill`, `project`) SHALL be removed from the filter list.

#### Scenario: Filter by case category

- **WHEN** the user selects "任务经验" in the category filter
- **THEN** the API SHALL be called with `category=case`
- **AND** only items with `category="case"` SHALL be returned

#### Scenario: No stale categories in filter

- **WHEN** the user opens the category filter dropdown
- **THEN** the options SHALL NOT include "通用 (general)", "技能 (skill)", or "项目 (project)" as distinct from the empty-string "通用"
- **AND** the options SHALL include "任务经验 (case)"
