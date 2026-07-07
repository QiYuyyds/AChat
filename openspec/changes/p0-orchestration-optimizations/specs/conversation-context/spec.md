# Spec Delta: Conversation Context

## ADDED Requirements

### Requirement: Tool result history SHALL be pruned by token size

When building adapter input history, AgentRunner MUST prune old tool_result content that exceeds a token threshold, replacing it with a truncation marker while preserving recent tool results in full.

#### Scenario: Old large tool result is pruned

- **WHEN** history contains a tool_result older than the recent N turns (default 3)
- **AND** the tool_result estimated token count exceeds the prune threshold (default 2000)
- **THEN** the tool_result content is replaced with `[tool_result 已裁剪, 详见 message_id=xxx]`
- **AND** recent tool_results within the last N turns remain unmodified.

#### Scenario: Small old tool result is preserved

- **WHEN** history contains a tool_result older than the recent N turns
- **AND** the tool_result estimated token count is below the prune threshold
- **THEN** the tool_result content remains in full.

#### Scenario: Pruned marker includes message reference

- **WHEN** a tool_result is pruned
- **THEN** the replacement marker includes the original message id
- **AND** the LLM can retrieve the full content by calling the relevant tool again or reading the artifact.

### Requirement: Old messages SHALL be folded when count exceeds threshold

When building adapter input history, AgentRunner MUST fold old messages into a summary marker when the total message count exceeds a threshold, without invoking an LLM.

#### Scenario: History exceeds fold threshold

- **WHEN** the number of messages in history exceeds the fold threshold (default 30)
- **THEN** the oldest messages beyond the recent N (default 20) are replaced with a single system message `[已折叠 N 条消息 (时间 range)]`
- **AND** pinned messages are never folded regardless of age.

#### Scenario: History below fold threshold

- **WHEN** the number of messages in history is at or below the fold threshold
- **THEN** no folding occurs and all messages are passed through.

### Requirement: LLM compaction SHALL trigger on token or count threshold

The auto-compaction hook MUST trigger when either the uncompacted message count watermark OR the estimated token usage ratio exceeds its threshold.

#### Scenario: Token threshold triggers compaction

- **WHEN** the estimated token usage of uncompacted messages exceeds 87% of the model context limit
- **THEN** auto-compaction is triggered even if the message count watermark has not been reached.

#### Scenario: Count threshold triggers compaction

- **WHEN** the uncompacted message count reaches the watermark (default 10)
- **THEN** auto-compaction is triggered regardless of token usage.

## MODIFIED Requirements

### Requirement: Custom agents SHALL receive bounded chat history

CustomAgentAdapter runs MUST receive serialized conversation history within a model-aware token budget for ordinary user turns. History serialization MUST apply tool_result pruning and old-message folding before the token budget cut, so that the most relevant recent context is preserved.

#### Scenario: Conversation has long history

- **WHEN** AgentRunner builds adapter input
- **THEN** it prunes large old tool_results
- **AND** folds old messages when count exceeds the fold threshold
- **AND** trims remaining history to fit the model context window and output reserve.

#### Scenario: Conversation has large tool results

- **WHEN** history contains tool_results exceeding the prune threshold
- **THEN** old tool_results are replaced with truncation markers
- **AND** recent tool_results are preserved in full.
