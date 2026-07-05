## ADDED Requirements

### Requirement: Preference key SHALL be normalized on write

The Preference store MUST normalize keys via a synonym mapping before persisting, so that semantically identical keys (e.g. "喜欢", "偏好", "偏爱") collapse to a single canonical key (e.g. "喜好").

#### Scenario: Synonym key is written
- **WHEN** `preference.set("喜欢", "Python")` is called and "喜好" already exists
- **THEN** the system normalizes "喜欢" to "喜好" and updates the existing "喜好" entry instead of creating a new row.

#### Scenario: Unknown key passes through
- **WHEN** `preference.set("编程框架", "React")` is called and no synonym mapping exists
- **THEN** the key "编程框架" is persisted as-is without modification.

### Requirement: LLM preference extraction SHALL receive existing keys

The `extract_preferences` function MUST accept an `existing_keys` parameter and include the current key list in the LLM prompt, instructing the model to reuse semantically equivalent existing keys rather than creating synonyms.

#### Scenario: User states a preference similar to an existing one
- **WHEN** the user says "我偏爱函数式编程" and the Preference table already has key "喜好"
- **THEN** the LLM prompt includes `existing_keys=["喜好", ...]` and the model outputs `{"喜好": "函数式编程"}` reusing the existing key.

#### Scenario: No LLM available
- **WHEN** `generate_fn` is None and `existing_keys` is provided
- **THEN** the system falls back to rule-based extraction without using `existing_keys`, and the key normalization layer handles synonyms on write.

### Requirement: Preference table SHALL undergo periodic LLM consolidation

The memory subsystem MUST trigger LLM-based Preference consolidation when the entry count exceeds a threshold (default 15), merging semantically duplicate key-value pairs into a single canonical entry.

#### Scenario: Preference count exceeds threshold
- **WHEN** `_safe_consolidate` runs and `len(preference.data) > 15`
- **THEN** the system calls `_consolidate_preferences` which sends all Preferences to the LLM, receives merged results, and batch-updates the Preference table.

#### Scenario: Preference count below threshold
- **WHEN** `_safe_consolidate` runs and `len(preference.data) <= 15`
- **THEN** Preference consolidation is skipped; only LTM consolidation proceeds.

### Requirement: Tool call failures SHALL be visible to memory extraction

The `_post_run_memory_hook` MUST extract `tool_result` parts with `isError=True` from Message parts_list and append them to the agent text passed to `on_message_end`, so that `extract_memory_from_reply` can classify them as `tool_failure`.

#### Scenario: write_artifact fails with validation error
- **WHEN** a `write_artifact` call fails with "Invalid args: 3 validation errors" and the assistant text is "I'll create the artifact now."
- **THEN** `_post_run_memory_hook` appends `[工具执行错误]\nwrite_artifact 调用失败: {"error": "Invalid args..."}` to the agent text, and `classify_memory_content` matches "工具" and "失败" keywords, routing the fact to LTM as `tool_failure` category.

#### Scenario: No tool errors in the run
- **WHEN** all tool calls succeed (no `isError=True` parts)
- **THEN** `_post_run_memory_hook` passes only the text parts to `on_message_end` with no error block appended.
