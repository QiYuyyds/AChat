# Frontend Delta: Parallel Wave Columns

## ADDED Requirements

### Requirement: Message list SHALL render parallel wave tasks in side-by-side columns

When an Orchestrator dispatch plan has multiple tasks in the same wave (no dependency relationship), the message list MUST render their child-run messages in side-by-side columns. Each column represents one parallel task's execution. Tasks in different waves MUST be rendered as separate row groups stacked vertically.

#### Scenario: Two tasks run in parallel (same wave)

- **WHEN** the Orchestrator dispatches wave 0 with task t1 (agent A) and task t2 (agent B) simultaneously
- **THEN** messages from t1's child run and t2's child run render in two side-by-side columns
- **AND** each column has a header showing the agent avatar, agent name, and task id
- **AND** messages within each column are stacked vertically with reduced spacing (same as grouped messages)

#### Scenario: Tasks run in sequential waves

- **WHEN** wave 0 has task t1 (agent A) and wave 1 has task t2 (agent B) that depends on t1
- **THEN** wave 0's messages render first in a column group
- **AND** wave 1's messages render below in a separate column group
- **AND** the two wave groups are separated by standard spacing (16px)

#### Scenario: Three tasks in one wave

- **WHEN** wave 0 has tasks t1, t2, t3 assigned to three different agents
- **THEN** messages from all three child runs render in three side-by-side columns
- **AND** each column is approximately one-third width on wide screens

#### Scenario: Non-dispatch messages remain single-column

- **WHEN** a user message or a non-Orchestrator agent message appears in the conversation
- **THEN** it renders in the standard single-column layout
- **AND** it is not placed inside a multi-column wave group

### Requirement: Wave columns SHALL display a column header per task

Each column in a wave group MUST display a header at the top containing the agent's avatar, agent name, and the task id. The header MUST be visually distinct from the message bubbles below it. Messages within the column MUST NOT repeat the avatar and agent name (they use the grouped rendering mode).

#### Scenario: Column header renders agent identity

- **WHEN** a wave column is rendered for task t1 assigned to agent "Claude"
- **THEN** the column header shows Claude's avatar, the name "Claude", and the task id "t1"
- **AND** messages below the header hide their avatar and name (grouped mode)
- **AND** each message still displays its per-message token usage badge

### Requirement: Wave columns SHALL degrade to single column on narrow screens

On screens narrower than 768px (Tailwind `md` breakpoint), multi-column wave groups MUST stack vertically into a single column, maintaining the same message order as the wide-screen layout.

#### Scenario: User views on mobile-width screen

- **WHEN** the viewport width is less than 768px and a wave has 2 parallel tasks
- **THEN** the two columns stack vertically instead of side-by-side
- **AND** each stacked column still shows its column header

### Requirement: Wave computation SHALL use topological layering from plan dependencies

The wave number for each task MUST be computed from `DispatchPlanItem.dependsOn` relationships: tasks with no dependencies are wave 0, and each task's wave is one more than the maximum wave of its dependencies. Tasks in the same wave are candidates for side-by-side rendering.

#### Scenario: DAG with chained dependencies

- **WHEN** plan has t1 (no deps), t2 (no deps), t3 (depends on t1), t4 (depends on t2 and t3)
- **THEN** t1 and t2 are wave 0 (rendered side-by-side)
- **AND** t3 is wave 1 (rendered below wave 0)
- **AND** t4 is wave 2 (rendered below wave 1)
