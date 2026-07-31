## ADDED Requirements

### Requirement: Cross-run history pruning SHALL be ratio-aware

`build_history_for` MUST conditionally execute structural pruning (`prune_old_tool_results` and `fold_old_messages`) based on the ratio of loaded history tokens (including prompt estimate) to the model's context window size. The ratio threshold MUST be `0.65` (`PRE_RUN_COMPACT_RATIO`). When the ratio is below the threshold (or `model_context_limit` is unknown/zero), full history MUST be injected without pruning. When the ratio is at or above the threshold, existing structural pruning MUST execute as before.

`BuildHistoryOptions` MUST accept `model_context_limit` and `prompt_estimate` fields so that the calling context (`build_adapter_input`) can supply the model's context window size and system+prompt token estimate for ratio computation. Token estimation MUST use `estimate_full_message_tokens` operating on DB Message objects before serialization.

#### Scenario: Short conversation with large context model does not prune

- **WHEN** `build_history_for` loads 10-turn history (~80K tokens) for a model with 1M context window
- **AND** `prompt_estimate` is ~2K tokens
- **THEN** ratio = (80000 + 2000) / 1000000 = 0.082
- **AND** ratio < 0.65
- **AND** `prune_old_tool_results` is NOT called
- **AND** `fold_old_messages` is NOT called
- **AND** all tool_result content from prior runs is preserved verbatim in the serialized history

#### Scenario: Long conversation approaching 65% context triggers pruning

- **WHEN** loaded history tokens + prompt estimate reach 65% of the model's context window
- **THEN** `prune_old_tool_results` is called (replacing old tool_result with structured markers)
- **AND** `fold_old_messages` is called (folding old turns into a single fold marker)
- **AND** the most recent `KEEP_RECENT_TURNS = 2` complete turns remain intact

#### Scenario: Unknown model context limit defaults to no pruning

- **WHEN** `model_context_limit` is None or 0 in `BuildHistoryOptions`
- **THEN** ratio is set to 0.0
- **AND** full history is injected without pruning
- **AND** run-internal compaction (stage 1 at 0.70) serves as the fallback ratio check

#### Scenario: Small context model with short conversation

- **WHEN** a model with 8K context window has 3 tool-turn history (~5K tokens)
- **AND** `prompt_estimate` is ~1K tokens
- **THEN** ratio = (5000 + 1000) / 8000 = 0.75
- **AND** ratio ≥ 0.65 triggers pruning
- **AND** `prune_old_tool_results` prunes turn 1 (older than `KEEP_RECENT_TURNS = 2`)
- **AND** `fold_old_messages` does not trigger (turn count < `FOLD_TURN_THRESHOLD = 4`)

### Requirement: Session Memory SHALL be injected when no ContextSummary exists

When no `ContextSummary` (LLM compaction product) is available but `SessionMemory` has a non-empty summary, `build_history_for` MUST inject the Session Memory summary as a `<session_memory>` block at the head of the history items list. When both `ContextSummary` and `SessionMemory` exist, only `ContextSummary` MUST be injected (SessionMemory skipped to avoid duplicate summaries).

The `<session_memory>` block MUST include the `covers_up_to` timestamp attribute and MUST use a distinct tag from ContextSummary's `<conversation_summary>` to prevent LLM conflation of the two summary types.

Session Memory injection MUST NOT alter the message loading `WHERE` clause (no cut-off based on `covers_up_to`). Messages covered by the Session Memory summary may appear in both the summary and the raw history — this redundancy is acceptable.

#### Scenario: Session Memory available, no ContextSummary

- **WHEN** `build_history_for` runs and `latest_summary` (ContextSummary) is None
- **AND** `SessionMemory.get(conversation_id)` returns a record with a non-empty summary
- **THEN** a `<session_memory covers_up_to="...">` block is injected as the first item in the history items list
- **AND** the block is marked as pinned (not dropped by token budget trimming)

#### Scenario: Both ContextSummary and Session Memory exist

- **WHEN** `build_history_for` runs and `latest_summary` (ContextSummary) is not None
- **AND** `SessionMemory.get(conversation_id)` also returns a record
- **THEN** only the `<conversation_summary>` block is injected
- **AND** the `<session_memory>` block is NOT injected

#### Scenario: Neither ContextSummary nor Session Memory exist

- **WHEN** `build_history_for` runs and neither summary is available
- **THEN** no summary block is injected
- **AND** history consists only of serialized messages (existing behavior unchanged)

#### Scenario: Session Memory does not affect message loading cut-off

- **WHEN** Session Memory with `covers_up_to = T` is injected
- **THEN** the message loading query does NOT add a `WHERE created_at > T` filter based on Session Memory
- **AND** messages with `created_at <= T` are still loaded and serialized (redundancy with summary is accepted)
- **AND** only `ContextSummary.covered_until_created_at` (when present) affects the message loading cut-off (existing behavior)
