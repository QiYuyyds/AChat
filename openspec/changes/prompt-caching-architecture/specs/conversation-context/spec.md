## ADDED Requirements

### Requirement: Slot SHALL have static flag for cache stability

The `Slot` dataclass MUST include a `static: bool` field (default `False`). Slots marked `static=True` are rendered into the system prompt (cache-stable). Slots marked `static=False` are rendered into the user message prefix (cache-safe dynamic injection).

#### Scenario: Static slot rendering
- **WHEN** a Slot with `static=True` has non-empty items after assembly
- **THEN** its content is included in `RuntimeContext.render_static()` output
- **AND** its content is NOT included in `RuntimeContext.render_dynamic()` output

#### Scenario: Dynamic slot rendering
- **WHEN** a Slot with `static=False` has non-empty items after assembly
- **THEN** its content is included in `RuntimeContext.render_dynamic()` output wrapped in `<system-reminder>` tags
- **AND** its content is NOT included in `RuntimeContext.render_static()` output

### Requirement: Schemas SHALL classify slots by change frequency

The 4 built-in schemas (CHAT, TOOL, REACT, RAG) MUST mark Constraints and Profile slots as `static=True`, and Planner, TaskMem, ToolState slots as `static=False`.

#### Scenario: REACT_SCHEMA slot classification
- **WHEN** the REACT_SCHEMA is inspected
- **THEN** SlotConstraints has `static=True`
- **AND** SlotProfile has `static=True`
- **AND** SlotPlanner has `static=False`
- **AND** SlotTaskMem has `static=False`
- **AND** SlotToolState has `static=False`

### Requirement: Dynamic context SHALL be injected into user message

AgentRunner MUST inject PromptAssembler's dynamic content (`render_dynamic()` output) as a prefix to the user message, wrapped in `<system-reminder>` tags. The system prompt MUST only receive static content (`render_static()` output).

#### Scenario: SDK agent run with PromptAssembler enrichment
- **WHEN** an SDK agent run triggers PromptAssembler enrichment
- **THEN** `system_prompt_with_workspace` is appended with `ctx.render_static()` output only
- **AND** the user `prompt` is prepended with `ctx.render_dynamic()` output
- **AND** if dynamic content is empty, the prompt is unchanged

### Requirement: RecallSource SHALL be removed from built-in schemas

The 4 built-in schemas (CHAT, TOOL, REACT, RAG) MUST NOT include `SlotRecall`. Semantic recall is triggered on-demand by the Agent through the `memory_recall` tool. The `RecallSource` class and its registration in `SourceRegistry` MAY remain in the codebase for future use.

#### Scenario: Chat mode schema without recall
- **WHEN** the CHAT_SCHEMA is inspected
- **THEN** it contains no Slot with `kind=SlotRecall`
- **AND** the Agent can still access semantic recall via the `memory_recall` tool

#### Scenario: Agent triggers recall on-demand
- **WHEN** an Agent calls the `memory_recall` tool with a query
- **THEN** the tool returns matching memories and preferences
- **AND** no automatic embedding search is performed during PromptAssembler assembly
