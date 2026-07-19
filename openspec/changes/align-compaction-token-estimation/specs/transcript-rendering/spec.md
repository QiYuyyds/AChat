## ADDED Requirements

### Requirement: Token estimation SHALL be unified via shared dict-format function

A shared `estimate_dict_message_tokens(msg: dict, include_reasoning: bool = False) -> int` function MUST be provided in `transcript_renderer.py`. It MUST count: `content` (string or text parts when list), `tool_calls[*].function.name` + `function.arguments`, and `reasoning_content` (only when `include_reasoning=True`). Each message MUST add a fixed 4-token overhead. The function MUST NOT count `role`, `tool_call_id`, `type`, or other JSON-structural fields. Tier 0's `estimate_messages_tokens` MUST delegate to this function with `include_reasoning=True`. Tier 4's message token estimation MUST delegate to this function with `include_reasoning=False`.

#### Scenario: Tier 0 delegates with reasoning included

- **WHEN** `compact_pipeline.estimate_messages_tokens` is called on an in-memory messages list containing reasoning_content
- **THEN** it delegates to `estimate_dict_message_tokens(msg, include_reasoning=True)` for each message
- **AND** reasoning_content tokens are included in the total

#### Scenario: Tier 4 delegates without reasoning

- **WHEN** Tier 4's `_estimate_chat_message_tokens` (replaced by `estimate_dict_message_tokens`) is called on a serialized chat message dict
- **THEN** it calls `estimate_dict_message_tokens(msg, include_reasoning=False)`
- **AND** reasoning_content is NOT counted (spec 13: thinking not replayed cross-run)

#### Scenario: Estimate excludes JSON metadata

- **WHEN** `estimate_dict_message_tokens` is called on a dict with `role`, `tool_call_id`, `type`, `content`, and `tool_calls`
- **THEN** only `content` and `tool_calls[*].function.name/arguments` + 4 overhead tokens are counted
- **AND** `role`, `tool_call_id`, and `type` field values are NOT counted

## MODIFIED Requirements

### Requirement: transcript_renderer SHALL document token estimation relationships

The `transcript_renderer.py` module MUST include a top-level docstring documenting the three message-level token estimators and their usage contexts:
1. `estimate_dict_message_tokens(dict)` — OpenAI dict format, used by Tier 0 (include_reasoning=True) and Tier 4 (include_reasoning=False)
2. `estimate_full_message_tokens(list[Message])` — DB Message format, used by Session Memory and Tier 2/3
3. `estimate_tokens(str)` — base function in `model_registry.py`, 4 chars ≈ 1 token

#### Scenario: Module docstring documents estimator relationships

- **WHEN** a developer reads `transcript_renderer.py`
- **THEN** the module docstring explains which estimator to use for Tier 0, Tier 2/3, Session Memory, and Tier 4
- **AND** the docstring explains why Tier 0 includes reasoning_content but Tier 4 does not
