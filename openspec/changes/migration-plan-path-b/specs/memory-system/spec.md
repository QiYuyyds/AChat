## ADDED Requirements

### Requirement: ShortTerm SHALL maintain a sliding window of recent turns
ShortTerm MUST use a `deque(maxlen=max_turns*2)` sliding window to store recent conversation turns (role + content + timestamp). It SHALL provide thread-safe `add()`, `get()`, `clear()`, and `count()` operations.

#### Scenario: Sliding window evicts oldest turns
- **WHEN** ShortTerm has `max_turns=10` and 11 user-assistant pairs are added
- **THEN** only the most recent 10 pairs (20 entries) remain in the deque

#### Scenario: ShortTerm is cleared between sessions
- **WHEN** `clear()` is called
- **THEN** the deque is emptied and count returns 0

### Requirement: LongTerm SHALL store and recall memories via embedding cosine similarity
LongTerm MUST persist memories to PostgreSQL with embedding vectors. `recall()` SHALL compute cosine similarity against a query embedding and return top-k results above a threshold.

#### Scenario: Memory is added and later recalled
- **WHEN** a fact is added via `add(content)` with a valid embedding
- **THEN** it is persisted to the `long_term_memory` table
- **AND** a subsequent `recall(query_embedding)` with high cosine similarity returns it

#### Scenario: Recall returns empty when no memories match
- **WHEN** `recall()` is called with a query embedding that has low cosine similarity to all stored memories
- **THEN** an empty list is returned

### Requirement: LongTerm SHALL perform three-phase consolidation
`consolidate()` MUST execute three phases: (1) decay — importance *= decay_rate^days_since_creation, (2) dedup+merge — cosine >= dedup_threshold triggers deletion, cosine >= similarity_threshold triggers merge (content concat + embedding weighted avg + importance max + tags dedup), (3) expire — age > ttl_days AND importance < min_importance triggers removal with graph centrality protection.

#### Scenario: Similar memories are merged
- **WHEN** two memories have cosine similarity >= 0.80 (similarity_threshold)
- **THEN** they are merged into one memory with concatenated content, weighted-average embedding, and max importance

#### Scenario: Near-duplicate memories are deduplicated
- **WHEN** two memories have cosine similarity >= 0.95 (dedup_threshold)
- **THEN** the less important one is deleted

#### Scenario: Old low-importance memories expire
- **WHEN** a memory is older than `ttl_days` (30) AND importance < `min_importance` (0.3)
- **THEN** it is removed from storage

#### Scenario: Graph-protected memories survive consolidation
- **WHEN** a memory node has in-degree >= `graph_protect_indegree` (3)
- **THEN** it is exempt from the expire phase even if age and importance conditions are met

### Requirement: LongTerm SHALL deduplicate on store
`store_classified()` MUST check cosine similarity against existing memories before inserting. If similarity >= `cosine_dedup_on_store` (0.95), the new memory SHALL NOT be inserted.

#### Scenario: Near-duplicate fact is rejected on store
- **WHEN** a new fact has cosine similarity >= 0.95 with an existing memory
- **THEN** the new fact is not inserted

### Requirement: Preference SHALL extract and persist user preferences
Preference MUST scan user messages for patterns (我喜欢/我爱/我叫 etc.) and store extracted key-value pairs in the `user_preferences` table with `user_id="default_user"`.

#### Scenario: User states a preference
- **WHEN** user sends "我喜欢 Python"
- **THEN** Preference extracts `{key: "language_preference", value: "Python"}` and persists it

#### Scenario: Preference context is built for prompt
- **WHEN** `build_context()` is called
- **THEN** all stored preferences are rendered into a structured text block

### Requirement: GraphMemory SHALL enhance memories via Neo4j graph traversal
GraphMemory MUST maintain memory nodes and edges (FOLLOWS/SIMILAR_TO/CAUSES/BELONGS_TO) in Neo4j. `find_related()` SHALL perform multi-hop traversal. `filter_protected()` SHALL identify high-indegree nodes exempt from consolidation deletion.

#### Scenario: Related memories are found via graph traversal
- **WHEN** `find_related(mem_id, hops=2)` is called
- **THEN** all memories reachable within 2 hops are returned

#### Scenario: GraphMemory degrades to no-op when Neo4j is unavailable
- **WHEN** Neo4j connection fails
- **THEN** all GraphMemory methods return empty/no-op without raising exceptions

### Requirement: MemoryService SHALL orchestrate memory lifecycle
MemoryService MUST assemble STM + LTM + Preference + GraphMemory. `initialize()` SHALL restore state from storage. `on_message_end()` SHALL extract facts from agent reply, extract preferences from user message, and trigger consolidation when needed.

#### Scenario: Full memory lifecycle on message completion
- **WHEN** `on_message_end(user_msg, agent_reply)` is called
- **THEN** facts are extracted from agent_reply and added to LTM
- **AND** preferences are extracted from user_msg
- **AND** consolidation is triggered if LTM count reaches `memory_consolidation_trigger` (5)

#### Scenario: Memory hook failure does not block conversation
- **WHEN** `on_message_end()` raises an exception
- **THEN** the exception is logged as warning and does not affect the conversation response
