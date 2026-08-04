# Persistence

## MODIFIED Requirements

### Requirement: ModelProfile SHALL be a user-scoped persistent entity

A `model_profiles` table MUST persist ModelProfile records with columns: `id` (PK), `user_id` (FK to users, CASCADE), `name`, `provider`, `model_id`, `api_key`, `api_base_url`, `is_default`, `supports_vision`, `last_test_status`, `last_tested_at`, `created_at`, `updated_at`, `cache_style` (nullable varchar(16)), and `detected_cache_style` (nullable varchar(16)). The table is a local table (SQLite in dual-DB mode). A one-time migration (`_migrate_agent_model_profiles`) SHALL copy baked-in model config from pre-migration `agents` rows into `model_profiles`, deduplicating by `(user_id, provider, model_id)`.

The `cache_style` and `detected_cache_style` columns are nullable with no default value and no backfill. They are only meaningful for `openai-compatible` provider profiles; for known providers, the adapter hardcodes the cacheStyle and these columns are ignored. The migration script SHALL NOT set `cache_style` or `detected_cache_style` on migrated profiles (they default to NULL = auto-detect).

#### Scenario: ModelProfile is created

- **WHEN** a user creates a ModelProfile via the API
- **THEN** a row is inserted into `model_profiles` with the user's `user_id`
- **AND** `is_default` is set to true if it is the user's first profile
- **AND** `cache_style` is NULL and `detected_cache_style` is NULL.

#### Scenario: cache_style column migration is idempotent

- **WHEN** the backend starts and the `cache_style` column already exists
- **THEN** the `ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS cache_style TEXT` statement is a no-op
- **AND** existing profiles retain their existing `cache_style` values (NULL for pre-change profiles).

#### Scenario: detected_cache_style is populated by adapter

- **WHEN** the adapter auto-detects the cache style for an `openai-compatible` profile
- **THEN** an `UPDATE model_profiles SET detected_cache_style = ? WHERE id = ?` is executed
- **AND** the update is idempotent (same detection result on repeated runs).

#### Scenario: Old agent model config is migrated

- **WHEN** the backend starts and `agents.model_provider` column still exists
- **THEN** the migration scans agents with non-null model config
- **AND** creates deduplicated ModelProfile records per user
- **AND** marks the earliest-created profile as default
- **AND** skips builtin agents (user_id IS NULL)
- **AND** `cache_style` and `detected_cache_style` are NULL on all migrated profiles.
