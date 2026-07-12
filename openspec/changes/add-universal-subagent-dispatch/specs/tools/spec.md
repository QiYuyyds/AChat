# Tools

## MODIFIED Requirements

### Requirement: task_dispatch SHALL support optional agentId with clone-self

The `task_dispatch` tool's `agentId` parameter SHALL be optional. When omitted (or equal to the caller's `agent_id`), the tool clones the calling agent. When specified in coordinated mode, it dispatches to a group member. The tool SHALL enforce dispatch depth limits and anti-loop rules.

#### Scenario: Agent clones itself without agentId
- **WHEN** `task_dispatch` is called without `agentId`
- **THEN** the handler uses the caller's `agent_id` as the target
- **AND** sets `dispatch_visibility='hidden'`
- **AND** spawns a child run with `dispatch_depth + 1`

#### Scenario: Orchestrator dispatches to a group member
- **WHEN** `task_dispatch` is called with `agentId` in coordinated mode
- **AND** the `agentId` is a member of the conversation
- **THEN** the handler sets `dispatch_visibility='visible'`
- **AND** spawns a child run with `dispatch_depth + 1`

#### Scenario: Subagent attempts to dispatch to another agent
- **WHEN** `task_dispatch` is called with `agentId` that differs from the caller's `agent_id`
- **AND** the caller is not in coordinated mode
- **THEN** the tool returns an error

#### Scenario: Dispatch at max depth
- **WHEN** `task_dispatch` is called and `dispatch_depth >= MAX_DISPATCH_DEPTH`
- **THEN** the tool returns an error: "Max dispatch depth reached"

#### Scenario: AgentId equals caller's own agent_id
- **WHEN** `task_dispatch` is called with `agentId` equal to `ctx.agent_id`
- **THEN** the handler treats it as a clone-self dispatch
- **AND** sets `dispatch_visibility='hidden'`
