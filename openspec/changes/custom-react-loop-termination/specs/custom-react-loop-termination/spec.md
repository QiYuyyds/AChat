## ADDED Requirements

### Requirement: Custom ReAct loop SHALL end primarily on model-done

The Custom (SDK) agent ReAct loop SHALL treat a model response with zero tool calls as the primary successful termination condition. The product MUST NOT use a small default hard step cap (such as 8 turns) as the normal completion condition for Custom agents.

#### Scenario: Model finishes without tools
- **WHEN** the Custom model returns a final assistant response with no tool calls
- **THEN** the run ends with an internal stop reason equivalent to `complete`
- **AND** no further tool-execution turns are started

#### Scenario: Default step cap is not the product default
- **WHEN** a Custom agent runs without an explicit `max_tool_turns` configuration
- **THEN** the loop MUST NOT stop solely because a built-in default of 8 (or similarly small) tool turns was reached

### Requirement: Custom loop SHALL apply a unified budget termination pipeline

The Custom loop SHALL evaluate context/token usage before each model invocation and apply thresholds in this order: mid-run compact (approximately 90% of context), soft wrap-up (approximately 92–93%), hard stop (approximately 95%). Soft wrap-up and forced final MUST each be attempted at most once per run when their triggers fire. If usage already exceeds the hard threshold but soft/forced have not yet run, the system MUST still attempt the wrap-up pipeline before permanently refusing new tool-execution turns.

#### Scenario: Soft wrap-up after compact band
- **WHEN** estimated context usage crosses the soft threshold after the compact threshold
- **THEN** the system injects a wrap-up instruction visible to the model
- **AND** the next model call still includes the full tool set for that agent

#### Scenario: Soft wrap-up content is hidden from the user chat UI
- **WHEN** a soft wrap-up instruction is injected
- **THEN** that instruction MUST NOT appear as a normal user-visible chat bubble

#### Scenario: Forced final after soft ignored
- **WHEN** soft wrap-up has been injected and the model still emits one or more tool calls
- **THEN** the system performs exactly one forced final model call with no tools available (or ignores tool calls)
- **AND** the forced final output is user-facing natural language covering completed work, remaining work, risks/blockers, and suggested next steps

#### Scenario: Hard stop after wrap-up
- **WHEN** the hard context threshold is reached and the wrap-up pipeline has completed or cannot proceed
- **THEN** the loop MUST NOT start a new tool-execution turn

### Requirement: Optional max_tool_turns SHALL use the same wrap-up pipeline

When `max_tool_turns` is explicitly configured to a positive integer, reaching that count MUST trigger the same soft → forced wrap-up pipeline used for budget exhaustion, rather than a silent hard cut without summary. When unset, no step-count fuse applies.

#### Scenario: Configured turn fuse fires
- **WHEN** `max_tool_turns` is set to N and the run reaches N tool-use turns
- **THEN** the system enters soft wrap-up (and forced final if tools continue)
- **AND** the user-visible stop label indicates an operation-turn limit

#### Scenario: Unset fuse
- **WHEN** `max_tool_turns` is unset
- **THEN** the run MUST NOT terminate solely due to a default tool-turn counter

### Requirement: Custom loop SHALL install behavioral circuit breakers

The Custom loop MUST detect and contain runaway tool patterns:

1. Identical tool name + stable argument fingerprint consecutively three times → inject a change-strategy/wrap-up signal; a further identical call MUST enter forced final.
2. The same tool name failing consecutively three times → inject then forced final on continued failure.
3. Mid-run compact failing consecutively three times → stop further compact attempts and enter the soft → forced pipeline.

Fingerprint stability SHOULD prefer sorted normalized arguments and MUST avoid over-triggering on volatile fields when practical; when uncertain, the system SHOULD under-trigger rather than mis-kill legitimate retries.

#### Scenario: Duplicate tool fingerprint trips
- **WHEN** the model invokes the same tool with the same stable fingerprint three times in a row
- **THEN** the system injects a strategy-change or wrap-up instruction for the next model call
- **AND** IF a fourth consecutive identical fingerprint occurs THEN forced final runs

#### Scenario: Same tool consecutive errors
- **WHEN** the same tool name returns execution errors three times consecutively
- **THEN** the system injects a recovery/wrap-up instruction
- **AND** IF failures continue for that tool THEN forced final runs

#### Scenario: Compact failure breaker
- **WHEN** mid-run compact fails three times consecutively in a run
- **THEN** the system MUST NOT retry compact again in that run
- **AND** it enters the soft → forced termination pipeline

### Requirement: Runs SHALL record stop_reason and user-facing stop labels

Every Custom run termination MUST record an internal `stop_reason` suitable for logs and evaluation. Non-`complete` terminations that surface to the product UI MUST provide a short Chinese user-facing label (light hint). Full enum strings MUST NOT be required in the default user UI.

#### Scenario: Budget forced final surfaces a light hint
- **WHEN** a run ends via forced final due to budget or a circuit breaker
- **THEN** internal telemetry includes a specific stop reason
- **AND** the UI can show a short Chinese explanation such as automatic summary due to context limits or repeated operations

#### Scenario: Natural completion has no alarm hint
- **WHEN** a run ends via model-done `complete`
- **THEN** the UI MUST NOT show a failure-style stop banner solely because the run ended

### Requirement: Nested Custom runs SHALL surface abnormal stops to parents

When a Custom subagent/child run ends with a non-`complete` stop reason, the tool result text returned to the parent MUST include a human-readable indication of abnormal termination (for example a stop reason tag), without requiring orchestrator API changes.

#### Scenario: Child forced final
- **WHEN** a dispatched Custom child run ends with `budget_forced_final` (or equivalent)
- **THEN** the parent tool result includes an explicit abnormal-stop marker or summary prefix
- **AND** still includes the child’s final user-facing text when available

### Requirement: Scope excludes CLI adapters and orchestration mode semantics

This capability applies only to the Custom/SDK ReAct execution path. Claude Code and Codex CLI adapter loops MUST keep vendor termination semantics. Solo/coordinated/subagent tool injection and DAG dispatch behavior MUST remain unchanged by this capability.

#### Scenario: CLI agent unchanged
- **WHEN** a Claude Code or Codex agent run completes
- **THEN** termination remains controlled by the vendor loop and existing AChat CLI timeouts/cancel behavior
- **AND** the Custom soft/forced pipeline is not required

#### Scenario: Orchestration tools still inject by mode
- **WHEN** a coordinated or solo run starts under unified agent loop modes
- **THEN** `task_dispatch` / `dispatch_plan` / plan tools continue to inject per existing mode rules
- **AND** Custom termination rules apply only inside each Custom execution run
