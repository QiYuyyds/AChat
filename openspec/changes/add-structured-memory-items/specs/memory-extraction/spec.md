## ADDED Requirements

### Requirement: LTM extraction prompt SHALL output summary and keywords

The LTM extraction prompt SHALL instruct the LLM to output a `summary` field (3-10 character Chinese or 3-10 word English self-contained title) and a `keywords` field (list of 3-5 retrieval keywords) for each extracted memory, alongside the existing `text` and `attributed_to` fields. The summary rules and keywords rules SHALL be explicitly specified in the prompt with examples.

#### Scenario: Extraction produces structured memory

- **WHEN** the LTM extraction processes a conversation where the user says "我们项目用 React 19"
- **THEN** the output JSON SHALL include `"summary": "项目前端框架"` and `"keywords": ["React", "React19", "前端框架"]`
- **AND** the `text` field SHALL still contain the full natural language memory statement

#### Scenario: Summary is self-contained

- **WHEN** the extraction produces a summary for a memory about deployment port conflicts
- **THEN** the summary SHALL be understandable without the full content (e.g., "部署端口冲突")
- **AND** the summary SHALL NOT be a generic word like "部署" or "配置"

### Requirement: Case memory extraction SHALL run at task completion

A case extraction function SHALL be triggered when a task or conversation completes, using the existing SessionMemory summary as input. The function SHALL call an LLM with a case extraction prompt to identify reusable task experiences (what worked, what failed, what patterns can be reused). The extraction SHALL only produce memories with long-term learning value; if no reusable experience is found, the function SHALL return an empty list and skip storage.

#### Scenario: Successful task produces case memory

- **WHEN** a refactoring task completes successfully and the SessionMemory summary describes "先跑全量测试确认基线，再分步修改"
- **THEN** the case extraction SHALL produce a memory with `category="case"` containing the reusable approach
- **AND** the memory SHALL be stored in LTM with `summary`, `keywords`, `content`, and `outcome` tag

#### Scenario: Trivial conversation produces no case memory

- **WHEN** a conversation consists of casual greetings with no task execution
- **THEN** the case extraction SHALL return an empty list
- **AND** no case memory SHALL be stored

### Requirement: Case extraction SHALL be configurable

The case extraction SHALL be controlled by a configuration flag `case_extraction_enabled` (default: enabled). When disabled, no case extraction SHALL run regardless of trigger conditions. The extraction SHALL only trigger when a SessionMemory summary exists; it SHALL NOT load full conversation history to extract cases.

#### Scenario: Case extraction disabled

- **WHEN** `case_extraction_enabled` is False
- **THEN** no case extraction SHALL run at task completion
- **AND** no additional LLM calls SHALL be made for case extraction

#### Scenario: Case extraction skips when no session summary

- **WHEN** a task completes but SessionMemory has no summary
- **THEN** case extraction SHALL be skipped
- **AND** no LLM call SHALL be made

### Requirement: Case memory SHALL use category "case"

Case memories SHALL be stored in LTM with `category="case"`. The `memory_store` tool SHALL accept `category="case"` as a valid category. When `category="case"` is specified, the `summary` and `keywords` parameters SHALL be required.

#### Scenario: Agent stores case memory via tool

- **WHEN** an Agent calls `memory_store` with `category="case"`, `summary="认证模块重构经验"`, `keywords=["重构", "认证", "JWT"]`
- **THEN** the memory SHALL be stored in LTM with `category="case"`
- **AND** the `summary` and `keywords` SHALL be persisted

#### Scenario: Case memory without summary is rejected

- **WHEN** `memory_store` is called with `category="case"` but no `summary`
- **THEN** the tool SHALL return an error indicating `summary` is required for case memories
