# Mobile Companion

## Purpose

Defines the planned Capacitor companion app and remote approval surface. Detailed planning lives in `specs/14-mobile-remote.md`.

## Requirements

### Requirement: Mobile companion SHALL connect with user authentication

The mobile app MUST authenticate via `/api/auth/login` to obtain a JWT. The JWT MUST be included as `Authorization: Bearer <token>` on all mobile API requests. The previous environment-variable pairing token (`AGENTHUB_MOBILE_TOKEN`) SHALL be deprecated.

#### Scenario: Mobile client logs in
- **WHEN** the mobile app submits email and password to `/api/auth/login`
- **THEN** it receives a JWT and user profile
- **AND** stores the token securely for subsequent requests.

#### Scenario: Mobile client accesses snapshot
- **WHEN** the app requests `/api/mobile/snapshot` with a valid Bearer token
- **THEN** it receives only conversations and data belonging to the authenticated user.

### Requirement: Mobile APIs SHALL use the same auth dependency as desktop

All mobile companion API routes MUST use the `get_current_user` FastAPI dependency, identical to desktop routes. Mobile-specific CORS and response shaping SHALL remain, but the auth gate MUST be unified.

#### Scenario: Mobile and desktop share auth
- **WHEN** a mobile client and desktop client both authenticate as the same user
- **THEN** they see the same conversations, agents, and data.

### Requirement: Mobile token migration SHALL be supported

During the transition period, AChat MAY accept both the legacy `AGENTHUB_MOBILE_TOKEN` bearer token and user JWTs on mobile endpoints. After migration is complete, only JWTs SHALL be accepted.

#### Scenario: Legacy mobile token is used during migration
- **WHEN** a mobile request arrives with the legacy pairing token
- **THEN** AChat accepts it (if `AGENTHUB_MOBILE_TOKEN` is set) but logs a deprecation warning.

#### Scenario: Legacy mobile token is rejected after migration
- **WHEN** `AGENTHUB_MOBILE_TOKEN` is not set in the environment
- **THEN** only valid user JWTs are accepted on mobile endpoints.

### Requirement: Mobile APIs SHALL expose conversation snapshots

Remote mobile APIs MUST provide a compact snapshot of conversations, messages, pending questions, pending writes, and run state needed for monitoring.

#### Scenario: User opens mobile app
- **WHEN** the app requests the snapshot endpoint
- **THEN** it receives current conversation state without direct database access.

### Requirement: Mobile app SHALL preview artifacts

The mobile app MUST let users open `artifact_ref` message parts and preview supported artifact types through authenticated mobile artifact APIs.

#### Scenario: User opens a web app artifact
- **WHEN** the user taps an artifact card in a conversation
- **THEN** the app fetches `/api/mobile/artifacts/:id`
- **AND** renders the web app in a sandboxed preview without direct database access.

### Requirement: Mobile conversation view SHALL stay readable during agent work

The mobile app MUST avoid expanding noisy intermediate agent work by default in the conversation timeline.

#### Scenario: Conversation contains multiple tool calls
- **WHEN** adjacent `tool_use` and `tool_result` message parts are rendered on mobile
- **THEN** the app groups them into a compact tool activity block
- **AND** multiple tool calls are collapsed by default while preserving completion and error state.

#### Scenario: User opens a conversation detail
- **WHEN** the mobile app loads the selected conversation messages
- **THEN** the timeline scrolls to the latest message automatically.

### Requirement: Mobile approvals SHALL map to server-side pending items

Mobile approval actions MUST call server APIs that resolve existing pending write or pending question records.

#### Scenario: User approves a pending write on mobile
- **WHEN** the mobile API receives approval
- **THEN** the desktop host applies the same ToolExecutor approval path as desktop UI.

### Requirement: Mobile feature SHALL remain optional

The desktop/web app MUST continue to function when no mobile companion is configured.

#### Scenario: No mobile token exists
- **WHEN** AChat starts
- **THEN** core chat, adapters, tools, and artifacts remain available.
