## ADDED Requirements

### Requirement: LTM memories SHALL be queryable via API
A `GET /api/memory/long-term` endpoint SHALL list long-term memory entries with optional filtering by `agent_id`, `category`, and `tag` parameters, and pagination via `page` and `size`. The response SHALL NOT include `embedding` fields.

#### Scenario: List all memories
- **WHEN** `GET /api/memory/long-term` is called without filters
- **THEN** all LTM entries are returned with pagination (default page=1, size=20)
- **AND** each entry includes: id, content, importance, category, tags, agent_id, scope, created_at, last_accessed
- **AND** embedding is NOT included in the response

#### Scenario: Filter by agent_id
- **WHEN** `GET /api/memory/long-term?agent_id=agt_123` is called
- **THEN** only entries with `scope='agent' AND agent_id='agt_123'` are returned
- **AND** global entries (scope='global') are NOT included

#### Scenario: Filter by category
- **WHEN** `GET /api/memory/long-term?category=fact` is called
- **THEN** only entries with `category='fact'` are returned

### Requirement: LTM memories SHALL be editable via API
A `PUT /api/memory/long-term/{id}` endpoint SHALL allow updating `content`, `importance`, `category`, and `tags` of an existing memory. When `content` is changed, the embedding SHALL be asynchronously recomputed.

#### Scenario: Edit content
- **WHEN** `PUT /api/memory/long-term/42` with `{"content": "updated text"}` is called
- **THEN** the memory's content is updated in PG
- **AND** an async task is triggered to recompute the embedding
- **AND** the in-memory Item is synchronized

#### Scenario: Edit importance only
- **WHEN** `PUT /api/memory/long-term/42` with `{"importance": 0.9}` is called
- **THEN** only importance is updated
- **AND** no embedding recomputation is triggered

#### Scenario: Non-existent memory
- **WHEN** `PUT /api/memory/long-term/99999` is called
- **THEN** a 404 error is returned

### Requirement: LTM memories SHALL be deletable via API
A `DELETE /api/memory/long-term/{id}` endpoint SHALL remove the memory from PG, the in-memory Item list, and the GraphMemory (Neo4j nodes/edges + PG mirror tables).

#### Scenario: Delete existing memory
- **WHEN** `DELETE /api/memory/long-term/42` is called
- **THEN** the LTM row is deleted from PG
- **AND** the corresponding MemoryNode and MemoryEdge rows are deleted
- **AND** the Neo4j node and its edges are deleted (if Neo4j is available)
- **AND** the in-memory Item is removed

#### Scenario: Delete non-existent memory
- **WHEN** `DELETE /api/memory/long-term/99999` is called
- **THEN** a 404 error is returned

### Requirement: Preferences SHALL be manageable via API
`GET /api/memory/preferences`, `PUT /api/memory/preferences/{key}`, and `DELETE /api/memory/preferences/{key}` endpoints SHALL allow listing, editing, and deleting user preferences.

#### Scenario: List preferences
- **WHEN** `GET /api/memory/preferences` is called
- **THEN** all preference key-value pairs are returned

#### Scenario: Edit preference value
- **WHEN** `PUT /api/memory/preferences/喜好` with `{"value": "TypeScript"}` is called
- **THEN** the preference value is updated in PG and in-memory

#### Scenario: Delete preference
- **WHEN** `DELETE /api/memory/preferences/喜好` is called
- **THEN** the preference is removed from PG and in-memory

### Requirement: Session Memory SHALL be viewable via API
A `GET /api/memory/session/{conversation_id}` endpoint SHALL return the Session Memory text for a given conversation. This endpoint is read-only.

#### Scenario: Session Memory exists
- **WHEN** `GET /api/memory/session/conv_123` is called
- **AND** a Session Memory record exists
- **THEN** the summary text and metadata are returned

#### Scenario: No Session Memory
- **WHEN** `GET /api/memory/session/conv_123` is called
- **AND** no Session Memory record exists
- **THEN** a 404 error is returned
