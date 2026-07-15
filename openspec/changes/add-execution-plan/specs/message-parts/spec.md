# Capability: message-parts (delta)

## ADDED Requirements

### Requirement: execution-plan-part-type

The `MessagePart` union type SHALL include a new branch:

```typescript
| {
    type: 'execution_plan'
    planId: string
    steps: PlanStep[]
    complexity: 'simple' | 'moderate' | 'complex'
  }
```

Where `PlanStep` is:
```typescript
interface PlanStep {
  id: string
  title: string
  status: 'pending' | 'in_progress' | 'done' | 'failed' | 'skipped'
}
```

#### Scenario: Part rendered as checklist card
- **WHEN** a message contains an `execution_plan` part
- **THEN** the frontend SHALL render it as a checklist card showing step titles with status icons (⬚ pending, 🔄 in_progress, ✅ done, ❌ failed, ⏭ skipped)

#### Scenario: Part not incremental
- **WHEN** an `execution_plan` part is created
- **THEN** it SHALL be pushed as a complete part via `part.start` event, with no `PartDelta` support

### Requirement: execution-plan-part-injection-path

The `execution_plan` part SHALL be injected by `consume_stream` upon receiving a `plan.created` event (symmetric to `artifact_ref` injection from `artifact.create`):

1. Tool handler returns result with planId
2. `_execute_tool_call_to_result` appends `PlanCreatedEvent`
3. `consume_stream` receives `plan.created` → pushes `execution_plan` part to `parts_buffer` → emits `part.start`

#### Scenario: Plan created event triggers part injection
- **WHEN** `consume_stream` receives a `plan.created` event with planId, steps, and complexity
- **THEN** it SHALL append an `execution_plan` part to the current message's parts buffer and emit a `part.start` event

### Requirement: execution-plan-history-serialization

When building LLM history from message parts, `execution_plan` parts SHALL be serialized as:

```
[执行计划: step1(✅), step2(🔄), step3(⬚)]
```

Using status emoji for compact token usage.

#### Scenario: History serialization
- **WHEN** `extract_text_from_parts` encounters an `execution_plan` part
- **THEN** it SHALL produce a compact one-line summary with step titles and status emojis
