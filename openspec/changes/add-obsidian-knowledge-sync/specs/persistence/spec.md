## MODIFIED Requirements

### Requirement: App settings SHALL be per-user storage

Per-user API keys and companion configuration MUST be stored in a `user_settings` table (PK = `user_id`) rather than a single-row `app_settings` table. Each user SHALL have their own `anthropic_api_key`, `openai_api_key`, `deepseek_api_key`, `ark_api_key`, `companion_mode`, `mobile_device_token`, and `obsidian_vault_path`.

#### Scenario: User saves OpenAI key in settings
- **WHEN** the settings API receives the key from an authenticated user
- **THEN** it normalizes empty strings to null
- **AND** stores the value in `user_settings` scoped to that user's `user_id`.

#### Scenario: User saves external deployment publishing settings
- **WHEN** the settings API receives `deployment_publish_enabled`, `deployment_publish_dir`, or `deployment_public_base_url`
- **THEN** these values are stored in `global_settings` (shared)
- **AND** normalizes empty strings to null.

#### Scenario: User saves Obsidian vault path
- **WHEN** the settings API receives `obsidian_vault_path` from an authenticated user
- **THEN** it normalizes empty strings to null
- **AND** stores the value in `user_settings` scoped to that user's `user_id`.

#### Scenario: Obsidian vault path is read for sync
- **WHEN** ObsidianSyncService initiates a sync
- **THEN** it reads `obsidian_vault_path` from `user_settings` for the authenticated user
- **AND** if the value is null or the path does not exist, the sync returns an error.
