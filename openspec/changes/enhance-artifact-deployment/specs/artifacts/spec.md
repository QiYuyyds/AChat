# Artifacts Delta

## ADDED Requirements

### Requirement: Web app deployments SHALL be materialized as local static packages

Each ready local deployment of a `web_app` artifact MUST write a static package under AChat-managed data storage and serve it from a stable deployment URL.

#### Scenario: User opens a local deployment URL
- **WHEN** the user opens `/deployments/{deploymentId}`
- **THEN** AChat serves the materialized web app entry document
- **AND** HTML responses use sandboxing and content-type safety headers.

#### Scenario: User downloads deployment packages
- **WHEN** the user requests the source or container download path from a ready deployment
- **THEN** AChat returns a ZIP file for the materialized deployment.
