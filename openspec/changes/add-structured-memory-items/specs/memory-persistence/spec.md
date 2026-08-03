## ADDED Requirements

### Requirement: LTM items SHALL carry structured summary and keywords fields

Each `Item` in `LongTerm` SHALL have a `summary` field (3-10 character Chinese or 3-10 word English self-contained title) and a `keywords` field (list of 3-5 retrieval keywords). The `summary` SHALL be understandable without the full content and SHALL act as a title capturing the memory's core topic. The `keywords` SHALL prefer proper nouns, core concepts, and technical terms, and SHALL NOT include generic words (e.g., "user", "project", "system").

#### Scenario: Structured memory item is created

- **WHEN** a new LTM item is stored via `store_classified` or `add`
- **THEN** the item SHALL have `summary`, `keywords`, and `content_scope` fields
- **AND** `summary` SHALL be a non-empty self-contained title
- **AND** `keywords` SHALL contain 3-5 specific retrieval terms

#### Scenario: Existing item without summary remains functional

- **WHEN** an item loaded from storage has an empty `summary`
- **THEN** the item SHALL still participate in recall via its existing `embedding`
- **AND** its keyword match score SHALL be 0.0

### Requirement: LTM items SHALL support optional content_scope field

Each `Item` SHALL have a `content_scope` field (string, default empty) representing an associated project/directory/file path. The field is optional; most memories do not require it. Only memories explicitly associated with a specific project context SHALL populate it.

#### Scenario: Project-scoped memory carries path

- **WHEN** a memory like "项目用 Next.js 16" is stored
- **THEN** `content_scope` MAY be set to the project path (e.g., `d:/java/project/agenthub`)
- **AND** the field is used for context isolation, not for PromptAssembler slot routing

#### Scenario: User-level memory has empty scope

- **WHEN** a memory like "用户叫张三" is stored
- **THEN** `content_scope` SHALL be empty
- **AND** no default value is forced

### Requirement: Embedding SHALL be computed from summary, not content

The `embedding` field of each LTM item SHALL be computed from the item's `summary` (not `content`). This concentrates the semantic signal into a shorter, more precise vector. The `content` field is retained for full-text return but does not participate in embedding computation.

#### Scenario: New memory embedding uses summary

- **WHEN** a new item with `summary="用户前端技术栈"` and `content="用户喜欢TypeScript，偏好React框架..."` is stored
- **THEN** `embedding` SHALL be `embed("用户前端技术栈")`
- **AND** `embedding` SHALL NOT be `embed(content)`

#### Scenario: Consolidation dedup uses content-level TF-cosine for confirmation

- **WHEN** consolidation checks two items for dedup with similarity ≥ `dedup_threshold`
- **THEN** the initial similarity check SHALL use summary embeddings
- **AND** a content-level `tf_cosine` check MAY be used as secondary confirmation to avoid false merges from short summaries

### Requirement: Recall SHALL use dual-path scoring

`LongTerm.recall()` SHALL score items using a combined formula: `score = semantic_similarity * 0.5 + keyword_match * 0.2 + importance * 0.3`. The semantic similarity SHALL compare query embedding against summary embeddings. The keyword match SHALL use Jaccard similarity between query tokens and item keywords. Items with combined score < 0.3 SHALL be filtered out.

#### Scenario: Summary embedding match dominates score

- **WHEN** recall is called with query "用户的技术栈" and an item has `summary="用户前端技术栈"` with `keywords=["TypeScript", "React"]` and `importance=0.7`
- **THEN** the semantic similarity (summary vs query) SHALL contribute 50% of the score
- **AND** the keyword match SHALL contribute 20% of the score
- **AND** the importance SHALL contribute 30% of the score

#### Scenario: Keyword match supplements semantic blind spot

- **WHEN** recall is called with query "TS 配置" and an item has `keywords=["TypeScript", "React"]`
- **THEN** the keyword Jaccard score SHALL be computed against tokenized query
- **AND** even if semantic similarity is low, the keyword match MAY bring the item above threshold

#### Scenario: Agent-scoped recall still applies dual-path scoring

- **WHEN** `recall(query, top_k=3, agent_id="agt_123")` is called
- **THEN** dual-path scoring SHALL be applied to agent-scoped items first, then global items for remaining slots
- **AND** the scoring formula is the same for both scopes

### Requirement: Keyword match SHALL use Jaccard similarity

The keyword match score SHALL be computed as the Jaccard similarity between the set of query tokens (lowercased) and the set of item keywords (lowercased). If either set is empty, the score SHALL be 0.0. No external dependencies (e.g., BM25, inverted index) SHALL be introduced.

#### Scenario: Exact keyword overlap

- **WHEN** query tokens are `["typescript", "配置"]` and item keywords are `["typescript", "react"]`
- **THEN** the Jaccard score SHALL be `1/3 ≈ 0.33` (intersection=1, union=3)

#### Scenario: No keyword overlap

- **WHEN** query tokens are `["天气"]` and item keywords are `["typescript", "react"]`
- **THEN** the Jaccard score SHALL be `0.0`

### Requirement: memory_recall tool SHALL return summary and keywords

The `memory_recall` tool handler SHALL include `summary` and `keywords` fields in each returned memory item, alongside the existing `content`, `importance`, `score`, and `category` fields. This provides the Agent with both the condensed title and full content in a single tool call.

#### Scenario: Recall response includes structured fields

- **WHEN** `memory_recall` is called and returns 3 memory items
- **THEN** each item SHALL contain `summary`, `keywords`, `content`, `importance`, `score`, and `category`
- **AND** no additional tool call is needed to fetch the full content

### Requirement: LongTermMemory DB model SHALL persist structured fields

The `LongTermMemory` SQLAlchemy model SHALL include `summary` (Text, default ''), `keywords` (ARRAY(Text), default []), and `content_scope` (Text, default '') columns. An optional partial index on `content_scope` SHALL be created for non-empty values.

#### Scenario: Structured fields are persisted to PostgreSQL

- **WHEN** a new LTM item with `summary="认证模块重构计划"` and `keywords=["JWT", "认证", "重构"]` is stored
- **THEN** the corresponding `long_term_memory` row SHALL have `summary='认证模块重构计划'` and `keywords='{"JWT","认证","重构"}'`

#### Scenario: Existing rows have default values

- **WHEN** the migration adds the new columns
- **THEN** all existing rows SHALL have `summary=''`, `keywords='{}'`, and `content_scope=''`

### Requirement: Existing memories SHALL be migrated to structured format

A background migration SHALL iterate existing LTM items that lack a `summary` and generate `summary` + `keywords` via an LLM call, then recompute the embedding from the generated summary. The migration SHALL be asynchronous, non-blocking on service startup, and resilient to individual failures (skip and continue). Items that fail migration SHALL retain their original content-based embedding and remain functional.

#### Scenario: Migration generates summary for existing item

- **WHEN** the migration runs on an item with `content="用户喜欢TypeScript"` and `summary=""`
- **THEN** an LLM call SHALL generate a `summary` (e.g., "用户技术栈偏好") and `keywords` (e.g., `["TypeScript"]`)
- **AND** the embedding SHALL be recomputed from the generated summary
- **AND** the updated fields SHALL be persisted to PostgreSQL

#### Scenario: Migration failure does not block other items

- **WHEN** the migration encounters an LLM error for item A
- **THEN** item A SHALL retain its original content-based embedding
- **AND** the migration SHALL continue to item B without interruption
