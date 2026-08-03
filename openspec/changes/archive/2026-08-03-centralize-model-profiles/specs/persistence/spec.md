## ADDED Requirements

### Requirement: model_profiles table SHALL store per-user model configurations

A `model_profiles` table SHALL store ModelProfile rows, each with `id`, `user_id`, `name`, `provider`, `model_id`, `api_key`, `api_base_url`, `is_default` (boolean), `supports_vision` (boolean), `last_test_status` (enum: untested/ok/fail), `last_tested_at` (nullable int ms), `created_at`, and `updated_at`. Rows SHALL be isolated by `user_id`. A uniqueness constraint SHALL ensure at most one `is_default=true` row per `user_id`.

#### Scenario: Two users have independent profiles

- **WHEN** user A and user B each create a ModelProfile
- **THEN** the profiles are stored with their respective `user_id` and are not visible to each other.

#### Scenario: Only one default per user

- **WHEN** a user marks a second profile as default
- **THEN** the previously-default profile has `is_default` set to false atomically.

## MODIFIED Requirements

### Requirement: Agent entity SHALL NOT store model fields

The `Agent` model SHALL NOT have `model_provider`, `model_id`, `api_key`, `api_base_url`, or `supports_vision` columns. These are removed. The `AgentRun` and `AgentRunCheckpoint` models retain `model_id` and `model_provider` as per-run records (populated from the resolved ModelProfile).

#### Scenario: Agent table after migration

- **WHEN** the migration completes
- **THEN** the `agents` table has no model-related columns
- **AND** `AgentRun.model_id` / `AgentRun.model_provider` are populated from the resolved ModelProfile at run time.

#### Scenario: Existing agent model data migrated to ModelProfiles

- **WHEN** the migration runs on a database with existing agents that have non-null `model_provider`
- **THEN** a ModelProfile is created per unique (user_id, provider, model_id, api_key, api_base_url) tuple
- **AND** each user's earliest-created profile is marked `is_default=true`.
