# Frontend Delta: Group Consecutive Agent Messages

## ADDED Requirements

### Requirement: Message list SHALL group consecutive agent messages from the same run

The message list MUST visually group consecutive agent messages that share the same `runId` and `agentId`. The first message in a group MUST display the full avatar, agent name, and timestamp. Subsequent messages in the same group MUST hide the avatar, agent name, timestamp, and streaming spinner, while preserving the message bubble content and per-message token usage badge.

#### Scenario: Agent produces multiple messages in one ReAct run

- **WHEN** an agent run produces multiple messages (e.g. thinking → tool_use → final text) with the same `runId` and `agentId`
- **THEN** the first message renders with full avatar, name, and timestamp
- **AND** each subsequent message in the group hides the avatar, name, timestamp, and streaming spinner
- **AND** each message in the group retains its per-message token usage badge
- **AND** the TurnTimeline renders only on the last message of the run

#### Scenario: Two different agents alternate in a group chat

- **WHEN** agent A sends a message, then agent B sends a message in the same conversation
- **THEN** agent B's message renders with full avatar, name, and timestamp
- **AND** the grouping from agent A is broken because `agentId` differs

#### Scenario: Agent message follows a user message

- **WHEN** a user sends a message and then an agent responds
- **THEN** the agent message renders with full avatar, name, and timestamp
- **AND** grouping is broken because `role` differs from the preceding user message

#### Scenario: Consecutive messages from different runs of the same agent

- **WHEN** the same agent produces messages in run X followed by messages in run Y
- **THEN** the first message of run Y renders with full avatar, name, and timestamp
- **AND** grouping is broken because `runId` differs

### Requirement: Grouped messages SHALL use reduced spacing

The message list MUST apply reduced vertical spacing (4px) between messages within the same group, and standard spacing (16px) between groups and between unrelated messages. The first message in the list MUST have no top margin.

#### Scenario: Three grouped messages followed by a user message

- **WHEN** messages are rendered as: agent_msg_1 (group start), agent_msg_2 (grouped), agent_msg_3 (grouped), user_msg_1 (new group)
- **THEN** the spacing between agent_msg_1 and agent_msg_2 is 4px
- **AND** the spacing between agent_msg_2 and agent_msg_3 is 4px
- **AND** the spacing between agent_msg_3 and user_msg_1 is 16px

### Requirement: Grouped messages SHALL preserve token usage display

Each agent message, whether grouped or not, MUST display its per-message token usage badge. The badge format and hover behavior MUST remain identical to ungrouped messages.

#### Scenario: A grouped message has token usage data

- **WHEN** a grouped (non-first) agent message has `usage` data
- **THEN** the token usage badge renders in the message's metadata area
- **AND** hovering the badge shows the same input/output/cache breakdown as ungrouped messages
