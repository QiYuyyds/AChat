## ADDED Requirements

### Requirement: LTM HTTP API SHALL serialize structured fields

The `GET /api/memory/long-term` endpoint SHALL include `summary` (string), `keywords` (array of strings), and `contentScope` (string) in each item of the response. The fields SHALL be camelCase in the JSON response. Items with empty `summary` SHALL return `summary: ""` (not null or omitted).

#### Scenario: API response includes structured fields

- **WHEN** the frontend calls `GET /api/memory/long-term`
- **THEN** each item in the response SHALL contain `summary`, `keywords`, and `contentScope` fields
- **AND** `keywords` SHALL be an array of strings (empty array if none)
- **AND** `contentScope` SHALL be a string (empty string if none)

#### Scenario: Unmigrated memory returns empty structured fields

- **WHEN** an LTM item has `summary=""` and `keywords=[]` (not yet migrated)
- **THEN** the API response SHALL include `summary: ""` and `keywords: []`
- **AND** the item SHALL still appear in the list with its `content` and other existing fields

### Requirement: LTM HTTP API update endpoint SHALL accept structured fields

The `PUT /api/memory/long-term/{id}` endpoint SHALL accept optional `summary`, `keywords`, and `contentScope` fields in the request body. Only non-null fields SHALL be updated; omitted fields SHALL remain unchanged. When `summary` changes, the endpoint SHALL trigger async embedding recompute using the new summary (consistent with the embedding-from-summary policy).

#### Scenario: User edits summary via API

- **WHEN** `PUT /api/memory/long-term/42` is called with `{"summary": "认证模块重构"}`
- **THEN** the item's `summary` SHALL be updated to "认证模块重构"
- **AND** an async embedding recompute SHALL be triggered using the new summary
- **AND** other fields (content, keywords, etc.) SHALL remain unchanged

#### Scenario: User edits keywords via API

- **WHEN** `PUT /api/memory/long-term/42` is called with `{"keywords": ["JWT", "认证"]}`
- **THEN** the item's `keywords` SHALL be updated to `["JWT", "认证"]`
- **AND** no embedding recompute SHALL be triggered (keywords do not affect embedding)

#### Scenario: Partial update omits structured fields

- **WHEN** `PUT /api/memory/long-term/42` is called with `{"content": "new content"}`
- **THEN** the item's `summary`, `keywords`, and `contentScope` SHALL remain unchanged

### Requirement: LongTerm.update_item SHALL accept structured field parameters

The `LongTerm.update_item()` method SHALL accept optional `summary: str | None = None`, `keywords: list[str] | None = None`, and `content_scope: str | None = None` parameters. Only non-None values SHALL update the in-memory `Item` and the persisted PG row.

#### Scenario: update_item with summary

- **WHEN** `update_item(memory_id=42, summary="新标题")` is called
- **THEN** the in-memory Item's `summary` SHALL be "新标题"
- **AND** the PG row SHALL be updated with `summary='新标题'`

#### Scenario: update_item without structured fields

- **WHEN** `update_item(memory_id=42, content="new content")` is called without summary/keywords/content_scope
- **THEN** the existing `summary`, `keywords`, `content_scope` values SHALL be preserved unchanged
