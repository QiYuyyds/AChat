# Desktop Setup Wizard

## Purpose

Defines the first-launch setup wizard that collects remote infrastructure connection details from the user, validates them, and persists the configuration for subsequent launches.

## Requirements

### Requirement: Setup wizard SHALL launch on first start

When no `server.json` exists in the data directory, the desktop app MUST show a setup wizard before starting the main application.

#### Scenario: First launch with no configuration
- **WHEN** AChat starts and `<userData>/data/server.json` does not exist
- **THEN** a setup wizard window is displayed
- **AND** the Python subprocess is NOT started until configuration is complete

#### Scenario: Subsequent launch with existing configuration
- **WHEN** AChat starts and `<userData>/data/server.json` exists
- **THEN** the setup wizard is skipped
- **AND** the application starts normally

### Requirement: Setup wizard SHALL collect infrastructure connection details

The wizard MUST allow the user to enter connection strings for all remote infrastructure services.

#### Scenario: User fills in connection details
- **WHEN** the setup wizard is displayed
- **THEN** the following fields are shown:
  - PostgreSQL URL (required)
  - Milvus host and port (optional)
  - Elasticsearch addresses (optional)
  - Neo4j URI, user, password (optional)
  - Redis URL (optional)
  - Phoenix endpoint (optional)
- **AND** only the PostgreSQL URL is required

### Requirement: Setup wizard SHALL test connectivity

The wizard MUST provide a "Test Connection" button that validates entered connection details.

#### Scenario: User clicks "Test Connection"
- **WHEN** the user clicks "Test Connection"
- **THEN** the wizard attempts to connect to each configured service in parallel
- **AND** displays a ✓ or ✗ icon next to each field
- **AND** PostgreSQL must show ✓ for the "Next" button to be enabled

#### Scenario: PostgreSQL connection fails
- **WHEN** the PostgreSQL connection test fails
- **THEN** the "Next" button is disabled
- **AND** an error message is shown below the field

### Requirement: Configuration SHALL be persisted as server.json

The wizard MUST save all connection details to `<userData>/data/server.json` upon completion.

#### Scenario: User completes the wizard
- **WHEN** the user clicks "Finish"
- **THEN** `server.json` is written to `<userData>/data/server.json`
- **AND** the wizard window is closed
- **AND** the main application starts

### Requirement: server.json format SHALL be versioned

The configuration file MUST include a version field to support future format migrations.

#### Scenario: server.json is created
- **WHEN** the wizard saves the configuration
- **THEN** the JSON includes a `"version": 1` field
- **AND** all connection fields are stored as top-level keys in camelCase

### Requirement: Setup wizard SHALL be implemented as a standalone Electron window

The wizard MUST NOT depend on Next.js or Python being available, as these processes start after configuration is complete.

#### Scenario: Wizard loads without backend
- **WHEN** the setup wizard is displayed
- **THEN** it renders from local HTML/CSS/JS bundled with the Electron app
- **AND** no HTTP requests to Next.js or Python are made

### Requirement: User SHALL be able to re-open the setup wizard

Users MUST be able to modify their server configuration after initial setup.

#### Scenario: User opens settings from the main UI
- **WHEN** the user navigates to Settings → Server Configuration
- **THEN** a "Reconfigure Server" button is available
- **AND** clicking it re-opens the setup wizard with current values pre-filled

#### Scenario: Configuration change requires restart
- **WHEN** the user saves new server configuration
- **THEN** the Python subprocess is restarted with new environment variables
- **AND** existing SSE connections are re-established
