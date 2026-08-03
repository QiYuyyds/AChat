## ADDED Requirements

### Requirement: ModelProfile SHALL be a user-scoped reusable model configuration

A `ModelProfile` is a named, per-user model configuration containing `provider`, `model_id`, `api_key`, `api_base_url`, `is_default`, `last_test_status`, and `last_tested_at`. ModelProfiles SHALL be isolated by `user_id`. A user MAY configure zero or more ModelProfiles. Each ModelProfile has a stable `id` that the conversation input bar and message runtime reference.

#### Scenario: User creates a ModelProfile with full credentials

- **WHEN** a user creates a ModelProfile with name, provider, model_id, api_key, and api_base_url
- **THEN** the profile is persisted to the `model_profiles` table scoped to the user's `user_id`
- **AND** `last_test_status` is `untested` and `is_default` is false (unless it is the user's first profile).

#### Scenario: User configures multiple profiles for the same provider

- **WHEN** a user creates two ModelProfiles both with provider `deepseek` but different model_id or api_key
- **THEN** both profiles coexist and are independently selectable in the input bar.

#### Scenario: User marks one profile as default

- **WHEN** a user sets `is_default=true` on a ModelProfile
- **THEN** any other profile for that user has `is_default` set to false
- **AND** the default profile is used when a message is sent without an explicit `modelProfileId`.

### Requirement: Runtime model resolution SHALL follow explicit → default → refuse priority

When an SDK (Custom adapter) agent runs, `build_adapter_input` SHALL resolve the model, provider, api_key, and api_base_url from a ModelProfile in this order: (1) the `modelProfileId` attached to the message if present, (2) the user's default ModelProfile if no explicit selection, (3) refuse to run the SDK agent with a clear error if the user has zero ModelProfiles configured.

#### Scenario: Message carries an explicit modelProfileId

- **WHEN** a message is sent with `modelProfileId` set to a valid profile id
- **THEN** `build_adapter_input` resolves model_id, model_provider, api_key, api_base_url, and supports_vision from that ModelProfile
- **AND** the run uses that model.

#### Scenario: Message sent without modelProfileId and user has a default profile

- **WHEN** a message is sent without `modelProfileId` and the user has at least one ModelProfile with `is_default=true`
- **THEN** `build_adapter_input` resolves the model from the default ModelProfile
- **AND** the run uses the default profile's model.

#### Scenario: User has zero ModelProfiles and sends a message to an SDK agent

- **WHEN** a message is sent to a Custom adapter agent and the user has zero ModelProfiles
- **THEN** the run is refused with an error message directing the user to configure a model in the Model tab.

#### Scenario: Referenced modelProfileId has been deleted

- **WHEN** a message references a `modelProfileId` that no longer exists
- **THEN** `build_adapter_input` falls back to the user's default ModelProfile
- **AND** emits a warning event that the referenced profile was missing.

#### Scenario: Default profile is deleted while other profiles remain

- **WHEN** a user deletes their default ModelProfile and at least one other profile remains
- **THEN** the earliest-created remaining profile is automatically marked as the new default.

### Requirement: Connectivity test SHALL perform a minimal chat completion ping

A `POST /api/model-profiles/{id}/test` endpoint SHALL send a minimal single-turn chat completion request using the profile's provider, model_id, api_key, and api_base_url, and return `status` (`ok` or `fail`), `latencyMs`, and optional `error`. The test request SHALL use `max_tokens=1` and a short timeout (5s). Test calls SHALL be rate-limited per profile to one test per 3 seconds.

#### Scenario: Profile credentials are valid

- **WHEN** the user tests a ModelProfile whose api_key, api_base_url, and model_id are all valid
- **THEN** the endpoint returns `status=ok` and the measured latency
- **AND** `last_test_status` is updated to `ok` and `last_tested_at` to now.

#### Scenario: Profile credentials are invalid

- **WHEN** the user tests a ModelProfile with an invalid api_key or unreachable base_url
- **THEN** the endpoint returns `status=fail` with the error reason
- **AND** `last_test_status` is updated to `fail`.

#### Scenario: Rapid repeated test requests

- **WHEN** the user triggers a connectivity test for the same profile within 3 seconds of the previous test
- **THEN** the request is rejected with a rate-limit error.
