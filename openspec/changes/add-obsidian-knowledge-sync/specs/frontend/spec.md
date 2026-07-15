## MODIFIED Requirements

### Requirement: Frontend SHALL consume server APIs and SSE

The frontend MUST use REST routes and SSE stream events; it SHALL not import or call LLM SDKs directly. All API calls MUST include authentication. The SSE connection MUST be established only after authentication is confirmed. The frontend MUST provide a knowledge library view with file-tree browsing for Obsidian-synced documents, a separate flat list for manually uploaded and agent-generated documents, Obsidian sync trigger, and settings panel for vault path configuration. Documents from different sources MUST be displayed in separate sections—Obsidian-synced documents in a tree view, manually uploaded and agent-generated documents in a flat list—never mixed together.

#### Scenario: User sends a message
- **WHEN** the UI posts to the messages API with a valid JWT
- **THEN** server-side AgentRunner invokes the adapter
- **AND** UI updates arrive through SSE events filtered by user.

#### Scenario: SSE connects after login
- **WHEN** the user logs in successfully
- **THEN** the frontend establishes the SSE connection with the auth cookie
- **AND** events are received only for the authenticated user.

#### Scenario: Knowledge library displays separated sections by source
- **WHEN** the user navigates to the knowledge library tab
- **THEN** the frontend renders two distinct sections: "Obsidian Vault" (tree view) and "我的文档" (flat list)
- **AND** the Obsidian Vault section calls `GET /api/documents/tree?path=` to fetch the root directory listing
- **AND** renders folders as expandable tree nodes and files as list items
- **AND** clicking a folder navigates into it by calling `GET /api/documents/tree?path=<folder_path>`
- **AND** the 我的文档 section calls `GET /api/documents/flat` to fetch manually uploaded and agent-generated documents
- **AND** renders them as a flat list sorted by updated_at DESC
- **AND** the two sections are visually separated and never mix documents from different sources

#### Scenario: User triggers Obsidian sync
- **WHEN** the user clicks the "Sync Obsidian" button in the knowledge library
- **THEN** the frontend calls `POST /api/obsidian/sync`
- **AND** displays a loading state during sync
- **AND** on success, shows a toast with the sync summary (added, updated, deleted, skipped)
- **AND** refreshes the Obsidian Vault tree view to reflect changes
- **AND** does not affect the 我的文档 flat list section

#### Scenario: User configures vault path in settings
- **WHEN** the user opens the settings panel and enters a vault path
- **THEN** the frontend calls `PATCH /api/settings` with `obsidian_vault_path`
- **AND** displays a path validity indicator (valid/invalid/empty)
- **AND** the "Sync Obsidian" button is disabled when no valid vault path is configured

#### Scenario: Obsidian sync status is displayed
- **WHEN** the user views the knowledge library
- **THEN** the frontend calls `GET /api/obsidian/status`
- **AND** displays last sync time and summary if available
- **AND** displays vault path and total .md file count
