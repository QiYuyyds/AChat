# Model Profiles

## MODIFIED Requirements

### Requirement: ModelProfile SHALL be a user-scoped reusable model configuration

A `ModelProfile` is a named, per-user model configuration containing `provider`, `model_id`, `api_key`, `api_base_url`, `is_default`, `last_test_status`, `last_tested_at`, and cache semantics fields. ModelProfiles SHALL be isolated by `user_id`. A user MAY configure zero or more ModelProfiles. Each ModelProfile has a stable `id` that the conversation input bar and message runtime reference.

For `openai-compatible` provider profiles, the profile MAY carry a `cacheStyle` field (`'deepseek'` | `'anthropic'` | `'none'` | `null`) where `null` means "auto-detect". The profile MAY also carry a `detectedCacheStyle` field (same union minus `null`) populated by the adapter's auto-detection logic on first successful LLM response. For known providers (`deepseek`, `anthropic`, `openai`, `volcano-ark`), `cacheStyle` and `detectedCacheStyle` are not user-configurable — the adapter hardcodes the style based on provider identity.

#### Scenario: User creates a ModelProfile with full credentials

- **WHEN** a user creates a ModelProfile with name, provider, model_id, api_key, and api_base_url
- **THEN** the profile is persisted to the `model_profiles` table scoped to the user's `user_id`
- **AND** `last_test_status` is `untested` and `is_default` is false (unless it is the user's first profile)
- **AND** `cacheStyle` is null (auto-detect) if provider is `openai-compatible`, or not applicable for known providers.

#### Scenario: User configures cacheStyle for openai-compatible profile

- **WHEN** a user creates or edits an `openai-compatible` ModelProfile and sets `cacheStyle` to `'deepseek'`
- **THEN** the adapter uses `'deepseek'` as the resolved cacheStyle for all runs using this profile
- **AND** the adapter does NOT perform auto-detection (user declaration takes priority)
- **AND** `detectedCacheStyle` remains null (not populated because user declared)

#### Scenario: Auto-detection populates detectedCacheStyle

- **WHEN** an `openai-compatible` ModelProfile with `cacheStyle=null` runs for the first time
- **AND** the LLM response contains `prompt_cache_hit_tokens` in usage
- **THEN** the adapter sets `detectedCacheStyle='deepseek'` on the ModelProfile (persisted)
- **AND** subsequent runs using this profile read `detectedCacheStyle` instead of re-detecting

#### Scenario: User overrides detectedCacheStyle

- **WHEN** a user manually sets `cacheStyle='none'` on a profile that previously had `detectedCacheStyle='deepseek'`
- **THEN** the adapter uses `'none'` (user declaration) for all subsequent runs
- **AND** `detectedCacheStyle` is cleared or ignored (user override takes priority)

#### Scenario: Known provider ignores cacheStyle fields

- **WHEN** a user creates a ModelProfile with provider `deepseek`
- **THEN** the cacheStyle selector is not shown in the UI
- **AND** the adapter hardcodes `cacheStyle='deepseek'` regardless of any `cacheStyle` or `detectedCacheStyle` values on the profile

#### Scenario: User configures multiple profiles for the same provider

- **WHEN** a user creates two ModelProfiles both with provider `deepseek` but different model_id or api_key
- **THEN** both profiles coexist and are independently selectable in the input bar.

#### Scenario: User marks one profile as default

- **WHEN** a user sets `is_default=true` on a ModelProfile
- **THEN** any other profile for that user has `is_default` set to false
- **AND** the default profile is used when a message is sent without an explicit `modelProfileId`.
