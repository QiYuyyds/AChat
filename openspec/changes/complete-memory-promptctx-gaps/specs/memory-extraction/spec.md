## ADDED Requirements

### Requirement: Memory classification SHALL fall back to LLM when rules do not match
When `classify_memory_content()` returns empty (no rule matched), the system SHALL call `llm_classify_memory()` to classify the content using LLM. The LLM classification SHALL support 7 categories (identity, preference, fact, episodic, tool_failure, policy, general) and 6 slot hints (profile, planner, task_memory, tool_state, constraints, recall_memory).

#### Scenario: Rule match — LLM not called
- **WHEN** classify_memory_content("用户名字", "Alice") returns ("identity", ["name"], "profile")
- **THEN** llm_classify_memory is NOT called, and the rule-based classification result is used

#### Scenario: Rule miss — LLM fallback succeeds
- **WHEN** classify_memory_content("项目架构", "微服务") returns ("", [], "") and generate_fn is available
- **THEN** llm_classify_memory is called with the content, and returns a category like "fact" with appropriate tags and slot_hint

#### Scenario: Rule miss — LLM unavailable
- **WHEN** classify_memory_content returns empty and no generate_fn is available
- **THEN** the content is classified as ("general", [], "") and stored via store_classified

#### Scenario: LLM classification parse failure
- **WHEN** llm_classify_memory calls LLM but the response is not valid JSON
- **THEN** the content falls back to ("general", [], "")

### Requirement: llm_classify_memory SHALL output structured JSON
`llm_classify_memory()` SHALL prompt the LLM with a classification instruction requesting JSON output with `category`, `tags`, and `slot_hint` fields. The function SHALL strip code fences from the response and parse JSON.

#### Scenario: Valid LLM classification response
- **WHEN** the LLM returns `{"category":"episodic","tags":["event","meeting"],"slot_hint":"recall_memory"}`
- **THEN** llm_classify_memory returns ("episodic", ["event", "meeting"], "recall_memory")

#### Scenario: LLM returns code-fenced JSON
- **WHEN** the LLM returns "```json\n{\"category\":\"fact\"}\n```"
- **THEN** code fences are stripped and JSON is parsed, returning ("fact", [], "")
