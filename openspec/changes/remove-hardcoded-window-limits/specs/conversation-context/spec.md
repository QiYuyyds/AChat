## MODIFIED Requirements

### Requirement: Custom agents SHALL receive bounded chat history

CustomAgentAdapter runs MUST receive serialized conversation history within a model-aware token budget for ordinary user turns. History loading MUST NOT be limited by a fixed message count (`DEFAULT_MAX_TURNS`). The DB query MUST load all uncompacted messages (filtered by ContextSummary cut-off when present). The token budget (`context_window - output_reserve - prompt_estimate`) MUST be the sole hard constraint on the size of serialized history sent to the LLM. When serialized history exceeds the token budget, the oldest non-pinned messages MUST be dropped until it fits.

`BuildHistoryOptions.max_turns` MUST default to `None` (no limit). External callers MAY explicitly set `max_turns` to restrict loading for testing or special scenarios, but the default behavior MUST load all uncompacted messages.

#### Scenario: Large context model loads all uncompacted messages

- **WHEN** `build_history_for` loads history for a model with 1M context window
- **AND** the conversation has 50 uncompacted messages totaling ~250K tokens
- **AND** `BuildHistoryOptions.max_turns` is None (default)
- **THEN** the DB query loads all 50 messages (no LIMIT clause)
- **AND** ratio = (250000 + prompt_estimate) / 1000000 ≈ 0.25
- **AND** ratio < 0.65 so no pruning executes
- **AND** token budget (≈990K) is not exceeded so no messages are dropped
- **AND** the LLM receives all 50 messages with full tool_result content

#### Scenario: Token budget drops oldest messages when over budget

- **WHEN** loaded history tokens exceed `token_budget` after pruning (or when ratio < 0.65 and no pruning runs)
- **THEN** the oldest non-pinned messages are dropped from the serialized output
- **AND** pinned messages are preserved regardless of position
- **AND** the total serialized tokens fit within `token_budget`

#### Scenario: Explicit max_turns override still works

- **WHEN** `BuildHistoryOptions.max_turns` is explicitly set to a positive integer by a caller
- **THEN** the DB query applies `.limit(max_turns)` as before
- **AND** only the most recent `max_turns` uncompacted messages are loaded

#### Scenario: ContextSummary cut-off limits loading scope

- **WHEN** a ContextSummary exists with `covered_until_created_at = T`
- **THEN** the DB query filters `WHERE created_at > T`
- **AND** only messages after the summary are loaded
- **AND** the summary block is injected at the head of the history items list

## ADDED Requirements

### Requirement: Auto-compact SHALL trigger on token threshold only

`_maybe_auto_compact_hook` MUST trigger `compact_conversation(silent=True)` when the estimated token count of uncompacted messages exceeds 87% of the model's context window. The message-count-based trigger (`AUTO_COMPACT_WATERMARK`) MUST be removed. The 87% token threshold MUST be the sole auto-compact trigger condition.

When `agent_id` is None or the model's context window is unknown, auto-compact MUST NOT trigger (safe degradation). A warning MUST be logged in this case.

#### Scenario: Large context model compacts only near capacity

- **WHEN** a conversation with a 1M context model has accumulated 150 messages (~750K tokens)
- **AND** 750K < 870K (87% of 1M)
- **THEN** auto-compact does NOT trigger
- **AND** the messages remain uncompacted for the next run

#### Scenario: Token threshold exceeded triggers compaction

- **WHEN** estimated uncompacted tokens exceed 87% of the model's context window
- **THEN** `compact_conversation(silent=True)` is called
- **AND** a ContextSummary is persisted
- **AND** the summary's `covered_until_created_at` becomes the cut-off for the next `build_history_for` call

#### Scenario: Small context model triggers early

- **WHEN** a model with 8K context window has 10 messages totaling 7,000 tokens
- **AND** 7,000 > 6,960 (87% of 8K)
- **THEN** auto-compact triggers
- **AND** the behavior is identical to the previous watermark-based trigger for small models

#### Scenario: Missing agent_id does not trigger compaction

- **WHEN** `_maybe_auto_compact_hook` is called with `agent_id = None`
- **THEN** auto-compact does NOT trigger
- **AND** a warning is logged indicating agent_id is required for token-based trigger
