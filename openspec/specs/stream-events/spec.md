# Stream Events

## Purpose

Defines the event contract connecting adapters, AgentRunner persistence, SSE transport, and frontend reducers. Detailed event shape lives in `specs/02-stream-events.md`.

## Requirements

### Requirement: StreamEvent SHALL be the only live update protocol

All agent output, tool activity, artifact creation, pending approvals, dispatch state, and usage updates SHALL flow through `StreamEvent` before reaching the UI. Each event SHALL carry the owning `user_id` so the EventBus can filter delivery to the correct SSE subscribers.

#### Scenario: Adapter emits text
- **WHEN** an adapter starts a text part
- **THEN** AgentRunner persists the part
- **AND** EventBus publishes the same event to SSE subscribers filtered by `user_id`.

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

### Requirement: Message streaming SHALL be bracketed

Each agent message MUST begin with `message.start` and finish with `message.end`, with part and tool events associated to the message id between those boundaries.

#### Scenario: Run completes normally
- **WHEN** the final adapter event has been consumed
- **THEN** the message status is updated to `complete`
- **AND** the run ends with status `complete`.

### Requirement: User messages SHALL be broadcast to all clients

A newly created user message MUST be published as a `message.added` event carrying the full message, so that clients other than the sender (e.g. a desktop client viewing a conversation a mobile client just posted into) insert it in real time. The message is already persisted by the time the event is published, so subscribers MUST apply it idempotently by message id rather than re-creating it.

#### Scenario: A second client receives another client's user message
- **WHEN** a user message is created from any client
- **THEN** EventBus publishes a `message.added` event with the full message row
- **AND** every other subscribed client upserts it by id
- **AND** the sending client (which already inserted it optimistically and reconciled via the POST response) is unaffected.

### Requirement: Message removals SHALL be broadcast to all clients

When messages are deleted server-side (withdraw, edit-and-resend, or regenerate), the deletion MUST be published as a `message.removed` event carrying the removed `messageIds` and `artifactIds`, so that clients other than the initiator drop them in real time. Subscribers MUST apply it idempotently (re-removing already-removed ids is a no-op), so the initiating client — which already reconciled via the HTTP response — is unaffected.

#### Scenario: A second client sees a withdraw/edit/regenerate
- **WHEN** withdraw, edit-and-resend, or regenerate deletes messages from any client
- **THEN** EventBus publishes a `message.removed` event with the deleted messageIds and artifactIds
- **AND** every other subscribed client removes those messages and artifacts by id.

### Requirement: Usage events SHALL update durable accounting

Adapters SHALL emit `message.usage` and `run.usage` when provider usage data is available, and AgentRunner MUST persist those payloads without coupling to provider-specific token fields.

#### Scenario: Codex reports turn usage
- **WHEN** Codex emits `turn.completed.usage`
- **THEN** the adapter emits `message.usage`
- **AND** the adapter emits `run.usage` with the effective model id.

### Requirement: Deployment events SHALL inject deploy status parts

Adapters SHALL emit `deploy.status` when an AChat deploy tool finishes, and AgentRunner MUST convert that event into a `deploy_status` message part.

#### Scenario: Deploy tool returns ready
- **WHEN** `deploy_artifact` or `deploy_workspace` returns a ready deployment record
- **THEN** the adapter emits `deploy.status`
- **AND** AgentRunner persists and publishes a `part.start` for `deploy_status`.

### Requirement: Bash approval events SHALL drive pending command UI

When a command approval gate is triggered, AChat MUST publish a pending command event and a resolved command event so all connected clients can render and clear the approval state.

#### Scenario: Key command waits for approval
- **WHEN** an agent requests a command that requires approval
- **THEN** AChat publishes `bash_command.pending` with the command, cwd, agent id, run id, and reason.

#### Scenario: Pending command is resolved
- **WHEN** the user approves, rejects, or the run is aborted
- **THEN** AChat publishes `bash_command.resolved`
- **AND** frontend reducers remove that pending command id.

### Requirement: Errors SHALL be visible in conversation state

Failures MUST be represented in both AgentRun status and conversation-visible message content.

#### Scenario: Provider rejects a request
- **WHEN** the adapter throws a provider error
- **THEN** AgentRunner marks streaming messages as `error`
- **AND** persists and publishes error `tool_result` events for unresolved tool calls in the run
- **AND** appends or creates a visible `[失败]` message.
