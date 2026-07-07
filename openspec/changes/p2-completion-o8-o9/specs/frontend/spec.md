# Spec Delta: Frontend

## ADDED Requirements

### Requirement: Message cards SHALL display turn-level metrics timeline

Agent message cards MUST display a turn metrics timeline component at the bottom of the message when `turn.metric` events were received for the associated run. The timeline MUST default to a collapsed single-line summary showing total turn count, total tokens, and total duration. When expanded, the timeline MUST display one bubble per turn, each showing the turn number, token count, tool icons (or names), and duration. Turns with duration or token usage exceeding 2x the average MUST be highlighted with a warning color.

#### Scenario: Collapsed timeline shows summary

- **WHEN** a message card renders with 5 turns of metric data
- **AND** the timeline is in collapsed state (default)
- **THEN** the card bottom shows a single line: "5 turns · X tokens · Ys"

#### Scenario: Expanded timeline shows per-turn bubbles

- **WHEN** the user clicks the collapsed timeline summary
- **THEN** the timeline expands to show 5 bubbles, one per turn
- **AND** each bubble displays turn number, token count, tool icons, and duration

#### Scenario: Anomalous turn highlighted

- **WHEN** turn 3 has `duration_ms=15000` and the average duration is 4000ms
- **THEN** turn 3's bubble is rendered with a warning color (e.g., amber)

#### Scenario: No turn metrics for CLI agent

- **WHEN** a message was produced by a CLI adapter run (no `turn.metric` events)
- **THEN** the message card does not render the turn timeline component

### Requirement: Store SHALL consume turn.metric events

The Zustand store MUST include a reducer for the `turn.metric` event type. The reducer MUST store turn metrics keyed by `runId` and `turn` number. When a `run.end` event is received, the turn metrics for that run MUST be finalized (no further updates expected).

#### Scenario: turn.metric event updates store

- **WHEN** a `turn.metric` event arrives with `runId="r1", turn=2, tokens={...}, tool_calls=[...], duration_ms=3000`
- **THEN** the store updates `runs["r1"].turnMetrics[2]` with the event payload

#### Scenario: run.end finalizes turn metrics

- **WHEN** a `run.end` event arrives for `runId="r1"`
- **THEN** `runs["r1"].turnMetrics` is marked as complete
- **AND** no further `turn.metric` events are expected for that run

### Requirement: Group chat dispatch cards SHALL display turn metrics in a collapsible panel

Orchestrator dispatch cards (showing child task results) MUST include a collapsible panel for turn metrics when the child task's run produced `turn.metric` events. The panel MUST be collapsed by default and expandable on click to show the same turn timeline as message cards.

#### Scenario: Dispatch card with turn metrics

- **WHEN** a child task's run produced 4 turns of metric data
- **AND** the dispatch card is rendered
- **THEN** the card includes a "Turn Metrics (4)" collapsible panel
- **AND** the panel is collapsed by default

#### Scenario: Dispatch card without turn metrics

- **WHEN** a child task's run was a CLI adapter (no turn metrics)
- **THEN** the dispatch card does not show the turn metrics panel
