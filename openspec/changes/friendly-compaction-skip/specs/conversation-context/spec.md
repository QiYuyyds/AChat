# Conversation Context

## ADDED Requirements

### Requirement: Compaction with nothing to do SHALL be a benign notice, not an error

When a compaction request cannot proceed for a benign reason (the conversation is too short, the compactable slice is below the size floor, or the conversation has no model-backed agent to summarize with), the system MUST NOT surface it as an error. It MUST return an HTTP 200 result marked as skipped, carrying a human-readable reason, so an accidental `/compact` produces a friendly message instead of a background error.

Genuine failures (conversation not found, summariser model returned empty) MUST still surface as errors.

#### Scenario: User triggers compaction on a too-short conversation

- **WHEN** `POST /conversations/{id}/compact` is called and the compactable slice is below the eligibility floor
- **THEN** the response is `200` with `skipped: true` and a `reason` string
- **AND** the response body does not carry a `summary` (no ContextSummary was written).

#### Scenario: User triggers compaction on a chat with no model-backed agent

- **WHEN** compaction is requested for a conversation whose agents cannot serve as a summariser (e.g. CLI-only chat)
- **THEN** the response is `200` with `skipped: true` and a reason explaining no summariser model is available.

#### Scenario: A genuine failure still errors

- **WHEN** the summariser model returns empty, or the conversation does not exist
- **THEN** the response is a `4xx`/`5xx` error, not a skipped result.

### Requirement: A benign compaction skip SHALL show an ephemeral system message

A benign skip MUST produce a `role="system"` message that is broadcast to connected clients and returned in the response for immediate display, but MUST NOT be persisted to the message store. The notice is transient by design: it informs the user in the moment and disappears on reload without polluting conversation history.

#### Scenario: Skip notice appears then does not persist

- **WHEN** a benign skip occurs
- **THEN** a system message stating the reason is broadcast and shown in the chat immediately
- **AND** reloading the conversation does not show that message (it was never written to the store).

#### Scenario: Frontend does not treat a skip as a failure

- **WHEN** the compact response has `skipped: true`
- **THEN** the frontend displays the returned system message and does NOT log an error
- **AND** it does not apply a post-compaction ctx override (no tokens were saved).
