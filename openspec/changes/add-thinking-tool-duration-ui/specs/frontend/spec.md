## ADDED Requirements

### Requirement: ThinkingPart SHALL display three visual states

The `ThinkingPart` component SHALL transition between three states based on message streaming status and user interaction:

1. **streaming-open**: When `message.status === 'streaming'` and the thinking part is the last content part in the array, the component SHALL display the full streaming content without collapsing, show a "深度思考中..." indicator, limit the visible content area to a maximum height (approximately 160px) with `overflow-y: auto`, and auto-scroll to follow new content as it arrives.

2. **completed-collapsed**: When the thinking part has ended (`endedAt` is set, or `message.status` is `'complete'`/`'error'`/`'aborted'`), the component SHALL automatically collapse to show zero lines of thinking content, display the thinking duration (computed as `endedAt - startedAt`), and provide an expand toggle button. The transition from streaming-open to completed-collapsed SHALL use a smooth CSS animation (max-height + opacity transition).

3. **user-expanded**: When the user clicks the expand toggle on a completed-collapsed thinking part, the component SHALL display the full thinking content with the duration label visible, and provide a collapse toggle button.

#### Scenario: Thinking streams during agent response

- **WHEN** an agent message has `status='streaming'` and the last part is a `thinking` part with content being appended
- **THEN** the ThinkingPart displays "深度思考中..." with the full streaming content visible
- **AND** the content area is limited to max-height ~160px with vertical scroll
- **AND** the content auto-scrolls to the bottom as new text arrives

#### Scenario: Thinking completes and auto-collapses

- **WHEN** the thinking part receives `endedAt` (via `part.end` event) or `message.status` transitions to `'complete'`
- **THEN** the ThinkingPart transitions to completed-collapsed state
- **AND** zero lines of thinking content are visible
- **AND** the duration is displayed as "已深度思考 · 12.3s" (computed from `endedAt - startedAt`)
- **AND** an "展开" (expand) toggle button is shown
- **AND** the transition uses a smooth CSS animation

#### Scenario: User expands completed thinking

- **WHEN** the user clicks the expand toggle on a completed-collapsed thinking part
- **THEN** the full thinking content is displayed
- **AND** the duration label remains visible
- **AND** the toggle button changes to "收起" (collapse)

#### Scenario: Thinking part lacks timing data

- **WHEN** a completed thinking part has no `startedAt` or `endedAt` fields (historical data)
- **THEN** the ThinkingPart renders in completed-collapsed state without duration display
- **AND** the label shows "已深度思考" without a duration suffix

### Requirement: ToolUsePart SHALL display duration for completed calls

The `ToolUsePart` component SHALL display the execution duration for completed tool calls, computed as `tool_result.endedAt - tool_use.startedAt`. The duration SHALL be shown in the tool call header next to the status label.

#### Scenario: Tool call completes with duration

- **WHEN** a `tool_result` arrives with `endedAt` for a `tool_use` part that has `startedAt`
- **THEN** the ToolUsePart header displays the duration (e.g., "已完成 · 3.2s" or "失败 · 3.2s")
- **AND** the duration is formatted using `formatDuration(ms)`

#### Scenario: Tool call without timing data

- **WHEN** a completed tool call lacks `startedAt` or `endedAt` (historical data)
- **THEN** the ToolUsePart header displays the status label without duration

### Requirement: ToolUsePart SHALL show live elapsed timer for running calls

The `ToolUsePart` component SHALL display a live elapsed timer when a tool call is in the `running` state (no `tool_result` yet received). The timer SHALL update every second using `setInterval`, showing the elapsed time since `tool_use.startedAt`.

#### Scenario: Running tool call shows live timer

- **WHEN** a `tool_use` part has `startedAt` and no matching `tool_result` has arrived
- **THEN** the ToolUsePart header displays "调用中 · Xs..." where X updates every second
- **AND** the timer uses the client-side `Date.now()` for the live count (not server timestamps)
- **AND** when `tool.result` arrives, the interval is cleared and the final server-computed duration replaces the live timer

#### Scenario: Running tool call without startedAt

- **WHEN** a `tool_use` part has no `startedAt` field (historical data) and no `tool_result`
- **THEN** the ToolUsePart header displays "调用中" without a timer suffix

### Requirement: ToolCluster SHALL display total duration

The `ToolCluster` component SHALL display the total time span of all contained tool calls, computed as `max(endedAt) - min(startedAt)` across all tools. When expanded, each individual ToolUsePart within the cluster SHALL also display its own duration.

#### Scenario: Cluster with all completed tools

- **WHEN** a ToolCluster contains tools that all have `startedAt` and `endedAt`
- **THEN** the cluster header displays the total duration (e.g., "工具调用 × 3 · 8.5s")
- **AND** when expanded, each tool shows its individual duration

#### Scenario: Cluster with running tools

- **WHEN** a ToolCluster contains one or more tools still in `running` state
- **THEN** the cluster header displays a live timer using the earliest `startedAt` across all running tools
- **AND** running tools show their individual live timers when expanded

### Requirement: Message SHALL display total run duration on completion

When an agent message transitions to a terminal status (`'complete'`, `'error'`, or `'aborted'`), the UI SHALL display the total run duration at the bottom of the message bubble, computed from the message's first part timestamp to the last part's end timestamp (or `message.end` event timestamp).

#### Scenario: Agent message completes

- **WHEN** an agent message with `status='complete'` has parts with timing data
- **THEN** the message bubble displays "本次回答共耗时 Xs" at the bottom
- **AND** the duration is computed from the earliest `startedAt` to the latest `endedAt` across all parts

#### Scenario: Message without timing data

- **WHEN** an agent message has parts without any timing fields (historical data)
- **THEN** no total run duration is displayed

### Requirement: Duration formatting SHALL be consistent

All duration displays SHALL use a shared `formatDuration(ms)` function with the following format rules:
- Less than 1000ms: display as `{ms}ms`
- Less than 60000ms: display as `{seconds.toFixed(1)}s`
- 60000ms or more: display as `{min}m{sec}s`

#### Scenario: Sub-second duration

- **WHEN** a duration is 832ms
- **THEN** it is formatted as "832ms"

#### Scenario: Multi-second duration

- **WHEN** a duration is 12300ms
- **THEN** it is formatted as "12.3s"

#### Scenario: Multi-minute duration

- **WHEN** a duration is 195000ms
- **THEN** it is formatted as "3m15s"

### Requirement: Message list SHALL display persistent run-level working indicator

The message list SHALL render a persistent working indicator at the bottom when one or more runs are in `running` status (using `useTopLevelRunningRuns`). The indicator SHALL remain visible throughout the entire run lifecycle — including gaps between ReAct turns where `message.status` is `'complete'` but the run has not ended — and SHALL be removed only when `run.end` fires.

The indicator SHALL include:
1. Agent avatar + name
2. An animated typing indicator (three bouncing dots)
3. A phase label describing what the agent is currently doing, inferred from the latest message's last part type and status
4. An elapsed timer showing total run duration since `run.startedAt`

#### Scenario: Run starts and indicator appears

- **WHEN** a `run.start` event arrives for a top-level run
- **THEN** the message list renders an `AgentWorkingIndicator` at the bottom
- **AND** the indicator shows the agent avatar, name, bouncing dots, "正在响应..." label, and elapsed timer

#### Scenario: Indicator persists during ReAct turn gap

- **WHEN** a message's status transitions to `'complete'` but the run is still `running`
- **THEN** the `AgentWorkingIndicator` remains visible
- **AND** the phase label updates to "准备下一轮..." or the next inferred phase
- **AND** the elapsed timer continues counting

#### Scenario: Phase label reflects thinking

- **WHEN** the latest agent message is streaming and its last part is a `thinking` part
- **THEN** the indicator phase label shows "深度思考中"

#### Scenario: Phase label reflects tool call

- **WHEN** the latest agent message has a `tool_use` part without a matching `tool_result`
- **THEN** the indicator phase label shows "调用工具: {toolDisplayName}"

#### Scenario: Phase label reflects text generation

- **WHEN** the latest agent message is streaming and its last part is a `text` part
- **THEN** the indicator phase label shows "生成回答中"

#### Scenario: Run ends and indicator disappears

- **WHEN** a `run.end` event arrives
- **THEN** the `AgentWorkingIndicator` for that run is removed from the message list

#### Scenario: Multiple agents running in parallel

- **WHEN** a group conversation has multiple top-level runs in `running` status simultaneously
- **THEN** each run renders its own `AgentWorkingIndicator` at the bottom of the message list
- **AND** each indicator shows its respective agent avatar, name, phase, and timer

### Requirement: Agent avatar SHALL pulse during active run

The agent avatar in `MessageItem` SHALL display a pulsing ring animation when the agent's run is in `running` status, not only when the individual message is `streaming`. This ensures the user sees the agent is still active during ReAct turn gaps.

#### Scenario: Avatar pulses during message streaming

- **WHEN** an agent message has `status='streaming'` and the run is `running`
- **THEN** the agent avatar displays a pulsing ring (`animate-pulse` + `ring-2 ring-primary`)

#### Scenario: Avatar pulses during ReAct turn gap

- **WHEN** an agent message has `status='complete'` but the run is still `running`
- **THEN** the agent avatar continues to display the pulsing ring
- **AND** the avatar only stops pulsing when `run.end` arrives

#### Scenario: Avatar without active run

- **WHEN** the agent's run has ended (`status='complete'`/`'failed'`/`'aborted'`) or there is no run
- **THEN** the agent avatar shows no pulsing ring
