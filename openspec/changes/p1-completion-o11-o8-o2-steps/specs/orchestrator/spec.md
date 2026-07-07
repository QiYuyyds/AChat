# Spec Delta: Orchestrator

## ADDED Requirements

### Requirement: DispatchPlanItem SHALL support advisory context_level field

`DispatchPlanItem` MUST include an optional `context_level` field (`Literal["isolated", "standard"] | None`) that controls the amount of cross-conversation context provided to the sub-agent. When `None` or `"isolated"`, the sub-agent receives the current default context (recent 5 messages + recent 5 artifacts). When `"standard"`, the sub-agent receives expanded context (recent 10 messages + all pinned messages + recent 10 artifacts). The field is advisory — `compile_and_validate_dispatch_plan` MUST NOT reject plans that omit it.

#### Scenario: Plan with context_level=standard

- **WHEN** the Orchestrator LLM produces a plan with `contextLevel: "standard"` on a review task
- **THEN** `compile_and_validate_dispatch_plan` accepts the plan without error
- **AND** `build_sub_agent_prompt` provides 10 recent messages, all pinned messages, and 10 recent artifacts to the sub-agent.

#### Scenario: Plan without context_level

- **WHEN** the Orchestrator LLM produces a plan without `contextLevel`
- **THEN** `compile_and_validate_dispatch_plan` accepts the plan
- **AND** `build_sub_agent_prompt` uses the default `isolated` strategy (5 recent messages + 5 recent artifacts).

#### Scenario: Plan with invalid context_level

- **WHEN** the Orchestrator LLM produces a plan with `contextLevel: "full"`
- **THEN** `compile_and_validate_dispatch_plan` accepts the plan (advisory field, not validated)
- **AND** `build_sub_agent_prompt` treats unknown values as `isolated`.

### Requirement: build_sub_agent_prompt SHALL provide standard context level

When `task.context_level == "standard"`, `build_sub_agent_prompt` MUST provide: the 10 most recent complete messages (instead of 5), all pinned messages for the conversation, and the 10 most recent existing artifacts (instead of 5). Upstream artifact summaries are unaffected by context_level.

#### Scenario: Standard context provides more recent messages

- **WHEN** a task with `context_level="standard"` is being dispatched
- **AND** the conversation has 15 recent messages and 3 pinned messages
- **THEN** `build_sub_agent_prompt` includes the 10 most recent messages and all 3 pinned messages in the prompt.

#### Scenario: Standard context provides more artifacts

- **WHEN** a task with `context_level="standard"` is being dispatched
- **AND** the conversation has 12 existing artifacts
- **THEN** `build_sub_agent_prompt` includes summaries of the 10 most recent artifacts (excluding upstream artifacts already covered).

#### Scenario: Isolated context unchanged

- **WHEN** a task with `context_level="isolated"` or `context_level=None` is being dispatched
- **THEN** `build_sub_agent_prompt` provides 5 recent messages, all pinned messages, and 5 recent artifacts (current behavior).

### Requirement: Orchestrator plan prompt SHALL guide context_level selection

The `ORCHESTRATOR_PLAN_SYSTEM_PROMPT` MUST include guidance for the LLM on when to set `contextLevel`: default is `isolated` for independent execution tasks; `standard` is recommended for review, debugging, or cross-module tasks that need more context.

#### Scenario: LLM sets contextLevel on review task

- **WHEN** the Orchestrator LLM plans a review task
- **AND** the plan prompt includes context_level guidance
- **THEN** the LLM includes `contextLevel: "standard"` in the review task's plan item.
