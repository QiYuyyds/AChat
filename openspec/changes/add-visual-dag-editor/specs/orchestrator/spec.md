## MODIFIED Requirements

### Requirement: Pending dispatch plan approval SHALL support user-edited plans

When `plan_approval_enabled` is `True`, the orchestrator's `dispatch_plan` tool parks the DAG in `PendingDispatchPlansStore` and awaits the user's decision. The user MAY approve as-is, reject, revise with natural-language feedback, OR approve with a modified plan. When a modified plan is provided, the system SHALL re-validate it via `validate_dag` before execution. If validation fails, the approve SHALL be rejected with an error message and the pending plan remains available for further editing.

#### Scenario: User approves plan as-is (unchanged)

- **WHEN** the user submits `action: "approve"` without a `plan` field
- **THEN** `PendingDispatchPlansStore.approve()` uses the original `pending_plan.plan`
- **AND** runs `_revalidation_validator` → `validate_dag` on the original plan
- **AND** on success, resolves the awaiting resolver with `PlanReviewOutcome(kind="approve", plan=compiled_plan)`

#### Scenario: User approves with modified plan

- **WHEN** the user submits `action: "approve"` with a `plan` field containing an array of `DispatchPlanItem`
- **THEN** `PendingDispatchPlansStore.approve(modified_plan=plan)` replaces the stored plan with the modified version
- **AND** runs the validator (`_revalidation_validator` → `validate_dag`) on the modified plan
- **AND** on success, resolves with `PlanReviewOutcome(kind="approve", plan=compiled_plan)`
- **AND** the `dispatch_plan` handler receives `outcome.plan` as the items to execute
- **AND** `execute_dag` executes the modified DAG via wave-based topological scheduling

#### Scenario: Modified plan fails validation

- **WHEN** the user submits `action: "approve"` with a `plan` that contains a cycle, duplicate id, self-dependency, or missing reference
- **THEN** `validate_dag` returns error strings
- **AND** `_revalidation_validator` raises `ValueError` with the joined errors
- **AND** `approve()` returns `PendingDispatchPlanResult(ok=False, error=<error message>)`
- **AND** the API returns HTTP 400 with the error message
- **AND** the pending plan remains in the store for further editing

#### Scenario: User revises with natural-language feedback (unchanged)

- **WHEN** the user submits `action: "revise"` with a `feedback` string
- **THEN** the system resolves with `PlanReviewOutcome(kind="revise", feedback=feedback)`
- **AND** the `dispatch_plan` handler returns `{ status: "revise_requested", feedback }`
- **AND** the orchestrator LLM re-plans based on the feedback

### Requirement: Modified plan agentId SHALL be verified against conversation membership

When a user submits a modified plan with `agentId` values, the system SHALL verify that all specified agents exist and belong to the conversation. This verification occurs at the `dispatch_plan` handler level after the `PlanReviewOutcome(kind="approve")` is received — the same `_verify_agents_in_conversation` check that runs for the original plan.

#### Scenario: User assigns task to agent not in conversation

- **WHEN** the user's modified plan includes a task with `agentId` pointing to an agent not in the conversation
- **THEN** the `dispatch_plan` handler's `_verify_agents_in_conversation` returns an error string
- **AND** the `dispatch_plan` tool returns an error result to the orchestrator LLM
- **AND** the LLM can inform the user or re-plan
