## MODIFIED Requirements

### Requirement: Database schema SHALL map domain entities
The PostgreSQL schema MUST persist agents, conversations, messages, artifacts, workspaces, attachments, agent runs, context summaries, app settings, long-term memories, user preferences, RAG chunks, chat history, memory nodes, and memory edges. The database driver SHALL be asyncpg via SQLAlchemy async engine instead of aiosqlite.

#### Scenario: New conversation is created
- **WHEN** a conversation is inserted
- **THEN** a workspace row is created or associated
- **AND** messages and runs can reference the conversation id.

#### Scenario: Database connects via asyncpg
- **WHEN** the application starts
- **THEN** the database engine uses `postgresql+asyncpg://` connection string
- **AND** connection pool is configured with `pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`

### Requirement: JSON columns SHALL store typed unions
JSON columns such as `messages.parts`, `artifacts.content`, and usage payloads MUST use PostgreSQL JSONB type instead of Text with JSON serializer/deserializer. JSONB columns SHALL support native operators (`->>`, `@>`, `jsonb_array_elements`) and correspond to TypeScript union types in shared code.

#### Scenario: Message parts are loaded
- **WHEN** the UI fetches messages
- **THEN** each part can be rendered by its discriminant without ad hoc parsing.

#### Scenario: JSONB query is performed
- **WHEN** a query filters on `messages.parts` using JSONB operators
- **THEN** the query executes using PostgreSQL native JSONB indexing

## ADDED Requirements

### Requirement: Six new ORM models SHALL be defined
The models module MUST define 6 new ORM models: `LongTermMemory` (content, importance, embedding, created_at, last_accessed, category, tags, slot_hint, score), `UserPreference` (user_id, key, value, updated_at), `RagChunk` (doc_hash, chunk_idx, content, parent_content, embedding, created_at), `ChatHistory` (role, content, created_at), `MemoryNode` (mem_id, content, importance), `MemoryEdge` (from_id, to_id, rel_type, weight).

#### Scenario: New tables are created on startup
- **WHEN** the application starts with `Base.metadata.create_all`
- **THEN** all 6 new tables are created in PostgreSQL alongside existing 9 tables

#### Scenario: LongTermMemory stores embedding as JSONB
- **WHEN** a LongTermMemory row is inserted
- **THEN** the embedding column stores the vector as JSONB array
- **AND** it can be retrieved and used for cosine similarity computation
