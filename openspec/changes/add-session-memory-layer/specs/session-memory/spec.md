## ADDED Requirements

### Requirement: Session Memory SHALL be incrementally maintained
A Session Memory layer SHALL be maintained per conversation. It SHALL be initialized when the conversation reaches `minimum_message_tokens_to_init` (default 10000) tokens, and updated when either `minimum_tokens_between_update` (default 5000) additional tokens accumulate or `tool_calls_between_updates` (default 3) tool calls occur since the last update.

#### Scenario: Session Memory is not initialized for short conversations
- **WHEN** a conversation has fewer than 10000 estimated tokens
- **THEN** no Session Memory record exists
- **AND** no extraction task is triggered

#### Scenario: Session Memory is initialized at threshold
- **WHEN** a conversation reaches 10000 tokens for the first time
- **THEN** a background task extracts a summary of recent messages
- **AND** a Session Memory record is created with `summary_type='session'`

#### Scenario: Session Memory is incrementally updated
- **WHEN** 5000 additional tokens accumulate since the last Session Memory update
- **THEN** a background task appends new content to the existing summary
- **AND** the Session Memory record is updated (not duplicated)

### Requirement: Session Memory extraction SHALL avoid tool-use chain midpoints
The extraction trigger SHALL check whether the last assistant turn contains an unresolved `tool_use` (no matching `tool_result`). If so, extraction SHALL be deferred until a natural breakpoint (non-tool assistant turn or tool_result received).

#### Scenario: Extraction deferred during tool chain
- **WHEN** the last assistant message contains a `tool_use` part without a corresponding `tool_result`
- **THEN** extraction is not triggered
- **AND** extraction will trigger on the next eligible breakpoint

### Requirement: Session Memory SHALL persist per conversation
Session Memory SHALL be stored in the `context_summaries` table with `summary_type='session'`. Each conversation SHALL have at most one active Session Memory record. The record SHALL be updated in-place (not appended) during incremental updates.

#### Scenario: Session Memory record is updated
- **WHEN** an incremental update completes
- **THEN** the existing Session Memory record's `summary_text` and `updated_at` are updated
- **AND** no new row is inserted

### Requirement: Session Memory SHALL degrade gracefully without LLM
When `_generate_fn` is not available or the LLM call fails, Session Memory extraction SHALL be silently skipped. The conversation flow SHALL not be affected.

#### Scenario: LLM unavailable
- **WHEN** `_generate_fn` is None
- **THEN** no Session Memory extraction is attempted
- **AND** Compaction falls back to the LLM-summarization path
