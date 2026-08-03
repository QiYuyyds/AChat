## MODIFIED Requirements

### Requirement: CustomAgentAdapter SHALL resolve model configuration from ModelProfile at runtime

CustomAgentAdapter SHALL resolve `model_id`, `model_provider`, `api_key`, `api_base_url`, and `supports_vision` from a ModelProfile passed via the message's `modelProfileId` (or the user's default ModelProfile) at runtime in `build_adapter_input`, NOT from the Agent entity. The Agent entity SHALL no longer store these fields.

#### Scenario: SDK agent run resolves model from explicit ModelProfile

- **WHEN** AgentRunner assembles the adapter input for a Custom adapter agent and the triggering message carries a `modelProfileId`
- **THEN** `model_id`, `model_provider`, `api_key`, `api_base_url`, and `supports_vision` are resolved from that ModelProfile
- **AND** `CustomConfig` is constructed from the profile, not the Agent.

#### Scenario: SDK agent run resolves model from default ModelProfile

- **WHEN** AgentRunner assembles the adapter input for a Custom adapter agent and the triggering message has no `modelProfileId`
- **THEN** the user's default ModelProfile supplies the model configuration.

#### Scenario: SDK agent run with zero ModelProfiles configured

- **WHEN** AgentRunner assembles the adapter input for a Custom adapter agent and the user has zero ModelProfiles
- **THEN** the run is refused with an error directing the user to configure a model.

### Requirement: ClaudeCodeAdapter and CodexAdapter SHALL NOT inject a model flag

CLI adapters (Claude Code, Codex) SHALL NOT inject `--model` from AChat. The `input.model_id` for CLI agents SHALL be None, and the CLI binary SHALL use its own locally-configured default model (OAuth account default). `DEFAULT_CLAUDE_MODEL` and the codex default remain only for usage-tracking backfill, not for flag injection.

#### Scenario: Claude Code CLI run does not pass --model

- **WHEN** AgentRunner assembles the adapter input for a Claude Code agent
- **THEN** the `--model` flag is NOT added to the CLI args
- **AND** the Claude CLI uses its own configured default model.

#### Scenario: Codex CLI run does not pass a model

- **WHEN** AgentRunner assembles the adapter input for a Codex agent
- **THEN** the `model` field in the JSON-RPC params is null
- **AND** the Codex CLI uses its own configured default model.

#### Scenario: CLI agent excluded from input bar model selection

- **WHEN** a conversation's only agent is a CLI adapter agent (Claude Code or Codex)
- **THEN** the input bar model selector is hidden or disabled
- **AND** no `modelProfileId` is attached to messages.

## REMOVED Requirements

### Requirement: CustomAgentAdapter SHALL resolve API key via four-layer chain

**Reason**: The per-Agent four-layer key chain (agent.api_key → user_settings → env → OAuth) is replaced by ModelProfile-based resolution. The Agent entity no longer stores api_key or api_base_url.

**Migration**: Existing agents' baked-in model/key/url are auto-migrated into ModelProfiles during the DB migration. `build_adapter_input` now reads from ModelProfile instead of Agent fields. The `user_settings` provider-key fields remain as the env-fallback for ModelProfile creation convenience but are no longer the runtime resolution path for SDK agents.
