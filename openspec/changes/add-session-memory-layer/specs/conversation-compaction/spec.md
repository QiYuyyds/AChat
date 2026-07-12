## MODIFIED Requirements

### Requirement: Compaction SHALL three-way branch on Session Memory coverage
When compacting a conversation, `compact_conversation` SHALL check for an existing Session Memory record (`summary_type='session'`) and branch based on coverage:

1. **Full coverage**: Session Memory's `covers_up_to` ≥ the last compactable message's `created_at` → use Session Memory's summary directly, skip LLM call.
2. **Partial coverage**: Session Memory exists but `covers_up_to` < last compactable message → extract the gap messages (those after `covers_up_to`), call LLM with gap transcript + Session Memory summary as prior.
3. **No Session Memory**: No session record exists → fall back to the current LLM-based summarization path with full uncompacted messages.

In all three cases, the subsequent steps (persist ContextSummary, cut messages, breakpoint protection, capability restoration) are identical.

#### Scenario: Full coverage — zero LLM call
- **WHEN** `compact_conversation` is triggered
- **AND** a Session Memory record exists with `covers_up_to` ≥ the last message to compact
- **THEN** the Session Memory's `summary` is used as the Summary text
- **AND** no LLM call is made for summarization

#### Scenario: Partial coverage — small LLM call
- **WHEN** `compact_conversation` is triggered
- **AND** a Session Memory record exists with `covers_up_to` < the last message to compact
- **THEN** only the gap messages (after `covers_up_to`) are rendered as transcript
- **AND** the LLM is called with the gap transcript + Session Memory summary as prior
- **AND** the input is smaller than the full uncompacted message set

#### Scenario: No Session Memory — backward compatible
- **WHEN** `compact_conversation` is triggered
- **AND** no Session Memory record exists
- **THEN** the current LLM-based summarization path is used with full uncompacted messages
- **AND** behavior is identical to before this change

### Requirement: Compaction SHALL protect tool_use/tool_result chain boundaries
When selecting messages to compact, the compaction service SHALL ensure the cut point does not fall inside a `tool_use` / `tool_result` pair. If the cut point would orphan a `tool_result` (without its preceding `tool_use`) or leave a pending `tool_use` (without its following `tool_result`), the cut point SHALL be moved backward to a safe boundary.

#### Scenario: Cut point would orphan a tool_result
- **WHEN** the cut point falls after a `tool_result` but before its corresponding `tool_use`
- **THEN** the cut point is moved backward to include the `tool_use`
- **AND** both messages are kept in the uncompacted section

#### Scenario: Cut point would leave a pending tool_use
- **WHEN** the cut point falls after a `tool_use` but before its corresponding `tool_result`
- **THEN** the cut point is moved backward to exclude the `tool_use`
- **AND** both messages are kept in the uncompacted section

### Requirement: Compaction SHALL restore capability context after summarization
After replacing old messages with a Summary, the compaction service SHALL inject a capability context block containing: currently available tool names, active file attachments, and active dispatch plan summary (if any). This ensures the model retains awareness of its available capabilities after history compression.

#### Scenario: Capability context is injected after compaction
- **WHEN** compaction completes
- **THEN** a system-reminder block is appended after the Summary message
- **AND** the block contains current tool names, attachment list, and dispatch plan status
