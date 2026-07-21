# Stream Events

## ADDED Requirements

### Requirement: GuideSideEffectEvent SHALL notify frontend of management side effects

AChat SHALL support a `guide_side_effect` StreamEvent type. When a management tool successfully executes a create/update/delete/refresh operation, the tool handler SHALL emit this event. The event SHALL carry `conversationId` (the guide conversation id), `target` (which panel to refresh: `agents` / `skills` / `mcp` / `documents` / `memory` / `profile` / `conversations`), `action` (`create` / `update` / `delete` / `refresh`), and an optional `payload`. The frontend SHALL refresh the corresponding panel upon receiving this event.

#### Scenario: Guide agent creates an agent
- **WHEN** `manage_agents(action=create)` succeeds
- **THEN** AChat emits a `guide_side_effect` event with `target='agents'`, `action='create'`
- **AND** the event is delivered to the guide conversation's SSE bucket
- **AND** the frontend re-fetches the agents list.

#### Scenario: Guide agent optimizes memories
- **WHEN** `manage_memory(action=optimize)` succeeds
- **THEN** AChat emits a `guide_side_effect` event with `target='memory'`, `action='update'`
- **AND** the frontend refreshes the memory panel.

#### Scenario: Guide side effect event is filtered by user_id
- **WHEN** a `guide_side_effect` event is published
- **THEN** the EventBus delivers it only to SSE subscribers matching the event's `user_id`
- **AND** other users do NOT receive the event.
