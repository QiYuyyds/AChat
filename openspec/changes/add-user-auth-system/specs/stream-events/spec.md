# Stream Events Delta: Authenticated SSE

## MODIFIED Requirements

### Requirement: StreamEvent SHALL be the only live update protocol

All agent output, tool activity, artifact creation, pending approvals, dispatch state, and usage updates SHALL flow through `StreamEvent` before reaching the UI. Each event SHALL carry the owning `user_id` so the EventBus can filter delivery to the correct SSE subscribers.

#### Scenario: Adapter emits text
- **WHEN** an adapter starts a text part
- **THEN** AgentRunner persists the part
- **AND** EventBus publishes the same event to SSE subscribers filtered by `user_id`.

## ADDED Requirements

### Requirement: SSE endpoint SHALL require authentication

The `/api/stream` endpoint MUST verify JWT authentication before establishing an SSE connection. In production (HTTPS), the JWT MUST be read from an HttpOnly cookie. In development (HTTP, cross-origin), the JWT MAY be passed as a `?token=` query parameter.

#### Scenario: Authenticated SSE connection
- **WHEN** a client connects to `/api/stream` with a valid JWT
- **THEN** AChat establishes the SSE connection
- **AND** only events matching the authenticated user's `user_id` are delivered.

#### Scenario: Unauthenticated SSE connection
- **WHEN** a client connects to `/api/stream` without a valid JWT
- **THEN** AChat returns HTTP 401 and does not establish the SSE connection.

### Requirement: EventBus SHALL filter events by user_id

The EventBus `subscribe()` method MUST accept a `user_id` parameter and filter published events so that each subscriber only receives events belonging to their user. Events published without a `user_id` (e.g., system-wide heartbeats) MUST be delivered to all subscribers.

#### Scenario: Two users have active SSE connections
- **WHEN** user A's agent emits a `part.delta` event
- **THEN** the event is delivered to user A's SSE connection
- **AND** the event is NOT delivered to user B's SSE connection.

#### Scenario: Heartbeat is broadcast
- **WHEN** the EventBus emits a heartbeat
- **THEN** all connected subscribers receive it regardless of `user_id`.
