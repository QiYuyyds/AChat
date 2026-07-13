## ADDED Requirements

### Requirement: Redis KV Metadata Cache

The system SHALL provide a Redis-backed read-through cache for low-churn metadata entities to eliminate redundant PostgreSQL queries within and across agent runs.

When Redis is available, entity lookups SHALL first check Redis; on cache miss, the system SHALL query PostgreSQL and backfill Redis with a TTL. When Redis is unavailable, lookups SHALL query PostgreSQL directly (current behavior).

#### Scenario: Cache hit on Agent lookup

- **WHEN** `get_agent(agent_id)` is called and Redis is available and `agent:{agent_id}` exists in Redis
- **THEN** the system SHALL return the cached Agent data without querying PostgreSQL
- **AND** the response latency SHALL be ~1ms (local Redis RTT)

#### Scenario: Cache miss on Agent lookup

- **WHEN** `get_agent(agent_id)` is called and Redis is available but `agent:{agent_id}` does not exist
- **THEN** the system SHALL query PostgreSQL for the Agent row
- **AND** it SHALL serialize and store the result in Redis with key `agent:{agent_id}` and TTL 300 seconds
- **AND** it SHALL return the Agent data

#### Scenario: Cache invalidation on Agent update

- **WHEN** an Agent is created, updated, or deleted in PostgreSQL
- **THEN** the system SHALL `DEL agent:{agent_id}` from Redis
- **AND** the next lookup SHALL miss the cache and re-fetch from PostgreSQL

#### Scenario: Cache degradation when Redis unavailable

- **WHEN** `infra.redis_client` is `None` and `get_agent(agent_id)` is called
- **THEN** the system SHALL query PostgreSQL directly (no cache check)
- **AND** no Redis operations SHALL be attempted

### Requirement: Cached Entities and TTLs

The system SHALL cache the following entities with the specified TTLs and invalidation triggers:

| Entity | Redis Key Pattern | TTL | Invalidation Trigger |
|---|---|---|---|
| Agent | `agent:{agent_id}` | 300s | Agent create/update/delete |
| UserSettings | `user_settings:{user_id}` | 300s | UserSettings update |
| Workspace | `workspace:{conversation_id}` | 300s | Conversation creation (rarely changes after) |
| UserPreference | `user_prefs:{user_id}` | 120s | Preference save_batch / consolidation delete |
| GlobalSettings | `global_settings` | 300s | GlobalSettings update (replaces in-memory `_global_cache`) |

#### Scenario: Agent TTL expiry

- **WHEN** `agent:{agent_id}` has not been accessed for 300 seconds
- **THEN** Redis SHALL evict the key automatically
- **AND** the next lookup SHALL miss the cache and query PostgreSQL

#### Scenario: UserSettings cache invalidation

- **WHEN** `update_user_settings(user_id, patch)` is called
- **THEN** after the PostgreSQL UPDATE, the system SHALL `DEL user_settings:{user_id}` from Redis
- **AND** if Redis is unavailable, this step SHALL be skipped silently

### Requirement: Cache Serializer

The system SHALL serialize cached entities as JSON (not Python pickle) to ensure cross-language compatibility and debugging convenience. SQLAlchemy ORM objects SHALL be serialized to their column values before storage.

#### Scenario: Agent serialization

- **WHEN** an Agent ORM object is cached
- **THEN** it SHALL be serialized to a JSON dict containing: `id`, `name`, `system_prompt`, `adapter_name`, `model_id`, `model_provider`, `api_key`, `api_base_url`, `tool_names`, `skill_names`, `mcp_server_ids`, `memory_enabled`, `user_id`, and other scalar columns
- **AND** the deserialized result SHALL be usable as a drop-in replacement for the ORM object in read-only contexts

#### Scenario: Deserialization safety

- **WHEN** cached JSON is deserialized and a field is missing (schema drift after update)
- **THEN** the system SHALL use the field's default value or `None`
- **AND** it SHALL log a warning about the missing field
