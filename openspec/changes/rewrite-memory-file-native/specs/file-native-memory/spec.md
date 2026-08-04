# File-native Memory

## ADDED Requirements

### Requirement: Memory workspace SHALL use Markdown files with frontmatter and wikilinks

All memory content SHALL be stored as Markdown files under `<agenthub-data>/memory/` with YAML frontmatter (name, description, agent_id, tags, importance, bucket, created_at, updated_at, source) and optional wikilinks (`[[path]]`). The `bucket` field SHALL be either `procedure` or `wiki`. Users and agents SHALL be able to directly read, write, and edit these files. User preferences (name, location, hobbies, etc.) are managed by the separate PG-backed Preference system and are NOT stored as Markdown files.

#### Scenario: Agent writes a memory node
- **WHEN** auto_memory or auto_dream writes a memory node
- **THEN** a Markdown file is created at the appropriate path under `digest/{bucket}/`
- **AND** the file contains valid YAML frontmatter with all required fields
- **AND** the file body is free-form Markdown with optional wikilinks

#### Scenario: User directly edits a memory file
- **WHEN** a user opens and edits a digest Markdown file in a text editor or the memory UI
- **THEN** the edit is preserved on disk
- **AND** the next auto_index run picks up the change and updates the BM25 and wikilink indexes

### Requirement: Memory workspace SHALL have three-level lifecycle

Memory SHALL flow through three levels: `session/` (raw conversations) → `daily/` (lightly processed cards) → `digest/` (refined long-term memory). Each level has a distinct purpose and retention policy.

#### Scenario: Conversation produces session data
C- **WHEN** a conversation turn ends
- **THEN** the raw conversation is appended to `session/<conv_id>.jsonl`
- **AND** PG Message table is also written (dual-write)

#### Scenario: auto_memory produces daily card
- **WHEN** auto_memory runs after a conversation
- **THEN** a daily card is written to `daily/<date>/<session_event>.md`
- **AND** the daily card frontmatter includes source session link

#### Scenario: auto_dream produces digest node
- **WHEN** auto_dream processes a daily card
- **THEN** a digest node is written to `digest/{bucket}/<name>.md`
- **AND** the digest node frontmatter includes source daily card path

### Requirement: Digest SHALL have two buckets

Digest memory SHALL be organized into two buckets: `procedure/` (how-to experience, supports shared and agent-scoped subdirectories) and `wiki/` (knowledge nodes, global). User personal facts/preferences are NOT stored in digest — they are managed by the PG-backed Preference system (`UserPreference` table) which provides structured key-value access (`prefs.get("姓名")`) with three-layer deduplication (synonym normalization + manual override protection + LLM merge).

#### Scenario: Procedure memory is agent-scoped
- **WHEN** auto_dream integrates a procedure unit with `agent_id = "ag_coder"`
- **THEN** the file is written to `digest/procedure/agents/ag_coder/<name>.md`
- **AND** frontmatter `agent_id` field is set to `"ag_coder"`

#### Scenario: Procedure memory is shared
- **WHEN** auto_dream integrates a procedure unit with `agent_id = null`
- **THEN** the file is written to `digest/procedure/shared/<name>.md`
- **AND** frontmatter `agent_id` field is `null`

### Requirement: Memory search SHALL filter by agent_id

When a memory search specifies an `agent_id`, results SHALL include: (1) all nodes with `agent_id = null` (global), and (2) all nodes with `agent_id` matching the specified value. Nodes with a different non-null `agent_id` SHALL be excluded.

#### Scenario: Agent searches for its own + global memory
- **WHEN** `search(query, agent_id="ag_coder")` is called
- **THEN** results include digest nodes where `agent_id` is null (global)
- **AND** results include digest nodes where `agent_id` is "ag_coder"
- **AND** results exclude digest nodes where `agent_id` is "ag_researcher"
