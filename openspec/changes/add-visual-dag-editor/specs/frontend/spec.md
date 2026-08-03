## ADDED Requirements

### Requirement: DispatchPlanCard SHALL render DAG as a visual graph

The `DispatchPlanCard` component SHALL render the dispatch plan as a visual DAG graph (nodes = tasks, edges = dependencies) using React Flow + dagre auto-layout, replacing the flat `PlanTaskList` list view. The graph SHALL display each task as a node containing: agent avatar, task ID, task description (line-clamped), status icon, and worktree/retry badges when present. Edges SHALL represent `dependsOn` relationships and SHALL be animated when the downstream task is `running`.

When the plan has 2 or fewer tasks with no dependencies, the component MAY fall back to the list view for simplicity.

#### Scenario: DAG with dependencies rendered as graph

- **WHEN** `DispatchState.plan` has 3+ tasks with `dependsOn` relationships
- **THEN** the component renders a React Flow canvas with dagre auto-layout
- **AND** each task is a Custom Node positioned by topological rank
- **AND** edges connect dependent tasks with directional arrows
- **AND** nodes display agent avatar, task ID, description, and status icon

#### Scenario: Simple plan falls back to list

- **WHEN** `DispatchState.plan` has 2 or fewer tasks AND no `dependsOn` in any task
- **THEN** the component renders the existing `PlanTaskList` list view
- **AND** no React Flow canvas is mounted

#### Scenario: Node status updates in real time

- **WHEN** a `dispatch.start` SSE event arrives for task `t1`
- **THEN** node `t1` transitions to `running` style (warning border, ring, spinner icon)
- **WHEN** a `dispatch.end` SSE event arrives with `status: "complete"`
- **THEN** node `t1` transitions to `complete` style (green border, check icon)
- **WHEN** a `worktree.created` event arrives for task `t1`
- **THEN** a worktree badge appears on node `t1`

### Requirement: DispatchDAGGraph SHALL support editing in pending review mode

When `DispatchState.reviewStatus` is `"pending"` AND `DispatchState.pendingPlanId` is set, the `DispatchDAGGraph` component SHALL enter editable mode. In editable mode, the user SHALL be able to: add nodes (double-click canvas), delete nodes (context menu), create dependencies (drag from node handle to another node), delete dependencies (click edge delete button), edit task description (double-click node), and change agent assignment (context menu or node edit panel). The component SHALL maintain a local `editedPlan` state and call `onPlanChange` on every mutation.

#### Scenario: User adds a new node

- **WHEN** the user double-clicks an empty area of the canvas in editable mode
- **THEN** a popover form appears with fields: Task ID, Agent (dropdown of conversation agents), Task description, Depends on (checkboxes of existing task IDs)
- **AND** on submit, a new `DispatchPlanItem` is appended to `editedPlan`
- **AND** `onPlanChange` is called with the updated plan
- **AND** dagre re-layouts the graph

#### Scenario: User creates a dependency by dragging

- **WHEN** the user drags from the bottom handle of node `t1` to the top handle of node `t3`
- **THEN** `t3.dependsOn` is updated to include `t1` (if not already present)
- **AND** `onPlanChange` is called with the updated plan
- **AND** an edge appears between the two nodes

#### Scenario: User deletes a dependency

- **WHEN** the user clicks the delete button on the edge between `t1` and `t3`
- **THEN** `t1` is removed from `t3.dependsOn`
- **AND** `onPlanChange` is called with the updated plan
- **AND** the edge disappears

#### Scenario: User deletes a node

- **WHEN** the user opens the context menu on node `t2` and selects "Delete"
- **THEN** node `t2` is removed from `editedPlan`
- **AND** all edges referencing `t2` (incoming and outgoing) are removed
- **AND** any `dependsOn` references to `t2` in other tasks are automatically cleared
- **AND** `onPlanChange` is called with the updated plan

### Requirement: Frontend SHALL validate DAG edits in real time

The `DispatchDAGGraph` component SHALL call `validateDagFrontend()` (a TypeScript mirror of `validate_dag`) on every `editedPlan` mutation. If validation errors exist, the "执行计划" (approve) button SHALL be disabled and the errors SHALL be displayed below the graph. The "拒绝" and "修改意见" buttons SHALL remain enabled regardless of validation state.

#### Scenario: User creates a cycle

- **WHEN** the user adds a dependency from `t3` to `t1` where `t1` already depends on `t3` (directly or transitively)
- **THEN** `validateDagFrontend` detects the cycle
- **AND** the "执行计划" button is disabled
- **AND** an error message "检测到环形依赖" is displayed

#### Scenario: User clears all errors

- **WHEN** the user removes the offending dependency
- **THEN** `validateDagFrontend` returns no errors
- **AND** the "执行计划" button is re-enabled

### Requirement: Approve with modified plan SHALL submit edited DAG

When the user clicks "执行计划" in editable mode and the `editedPlan` differs from the original `dispatch.plan`, the frontend SHALL call `approvePendingDispatchPlanWithPlan()` which sends `action: "approve"` with the `plan` field in the request body. If `editedPlan` is identical to the original, the frontend MAY call the existing `approvePendingDispatchPlan()` without the `plan` field.

#### Scenario: User approves with modifications

- **WHEN** the user has edited the DAG (added/removed nodes or edges, changed agent assignments or task descriptions)
- **AND** clicks "执行计划"
- **THEN** the frontend calls `approvePendingDispatchPlanWithPlan(conversationId, pendingPlanId, editedPlan)`
- **AND** the request body contains `{ action: "approve", plan: [...editedPlan] }`

#### Scenario: User approves without modifications

- **WHEN** the user has not made any edits to the DAG
- **AND** clicks "执行计划"
- **THEN** the frontend calls `approvePendingDispatchPlan(conversationId, pendingPlanId)`
- **AND** the request body contains `{ action: "approve" }` (no `plan` field)
