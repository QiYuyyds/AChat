## ADDED Requirements

### Requirement: System prompt SHALL contain only static content

The system prompt sent to the LLM MUST contain only static (cache-stable) content. Dynamic content that changes per turn (Planner, TaskMem, ToolState, Recall) MUST be injected into the user message, not the system prompt.

#### Scenario: Multiple turns in same conversation
- **WHEN** an SDK agent runs two consecutive turns in the same conversation with the same agent configuration
- **THEN** the system prompt is byte-identical across both turns (excluding agent config changes)
- **AND** dynamic context is prepended to the user message wrapped in `<system-reminder>` tags

#### Scenario: Agent with plan_tasks tool
- **WHEN** an SDK agent run selects mode "react" because `plan_tasks` is in the tool set
- **THEN** the system prompt contains only Constraints and Profile slots (static=True)
- **AND** Planner, TaskMem, ToolState slots are rendered into the user message prefix

### Requirement: Compaction SHALL reuse main conversation system prompt

The `_summarise()` compaction call MUST reuse the main conversation's system prompt to maximize cache prefix hits. The call MUST NOT pass tool definitions to avoid unintended tool calls during compaction.

#### Scenario: Compacting a long conversation
- **WHEN** `compact_conversation` triggers `_summarise()` on a conversation with 50k+ tokens of history
- **THEN** the LLM call messages are `[{system: parent_system_prompt}, {user: compaction_prompt + transcript}]`
- **AND** no `tools` parameter is passed to the API call
- **AND** the system prompt prefix matches the main conversation's system prompt

### Requirement: Cache metrics SHALL be tracked at aggregate level

The system MUST maintain a sliding-window aggregate of prompt cache hit rates across recent LLM calls. Metrics MUST include cache_read_tokens, cache_creation_tokens, and input_tokens per call.

#### Scenario: Normal operation with good cache hit rate
- **WHEN** the recent 20 LLM calls have an average cache hit rate above 50%
- **THEN** `cache_metrics.recent_hit_rate()` returns a value >= 0.5
- **AND** `cache_metrics.should_alert()` returns False

#### Scenario: Cache hit rate drops below threshold
- **WHEN** the recent 20 LLM calls have an average cache hit rate below 50%
- **THEN** `cache_metrics.should_alert()` returns True
- **AND** a warning log is emitted with the current hit rate

### Requirement: Cache metrics SHALL be exposed via API

The system MUST expose cache health metrics through an API endpoint for monitoring and debugging.

#### Scenario: Frontend queries cache metrics
- **WHEN** a GET request is made to the cache-metrics endpoint
- **THEN** the response contains `hit_rate` (float), `recent_requests` (int), and `alert` (bool)

### Requirement: cache_creation_tokens SHALL be populated for Anthropic-compatible providers

The `_RunUsage` dataclass's `cache_creation_tokens` field MUST be populated from the LLM response's cache creation token count, supporting both DeepSeek and Anthropic field name formats.

#### Scenario: Anthropic-compatible provider returns cache stats
- **WHEN** an LLM response includes `cache_creation_input_tokens` in the usage object
- **THEN** `run_usage.cache_creation_tokens` is incremented by that value
- **AND** the `RunUsageEvent` emitted to the frontend includes the correct `cache_creation_tokens`

#### Scenario: DeepSeek provider returns cache stats
- **WHEN** an LLM response includes `prompt_cache_hit_tokens` in the usage object
- **THEN** `run_usage.cache_read_tokens` is incremented by that value (existing behavior)
- **AND** `cache_creation_tokens` remains 0 (DeepSeek does not report cache creation)
