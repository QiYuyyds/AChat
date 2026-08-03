## MODIFIED Requirements

### Requirement: Agent entity SHALL be a persona-only definition

The `Agent` core entity SHALL consist of identity and persona fields only: `id`, `user_id`, `name`, `avatar`, `description`, `capabilities`, `system_prompt`, `adapter_name`, `tool_names`, `skill_names`, `is_builtin`, `is_guide`, `mcp_server_ids`, and bookkeeping fields. The model-related fields (`model_provider`, `model_id`, `api_key`, `api_base_url`, `supports_vision`) are removed from the Agent entity. Model selection is a runtime concern resolved from ModelProfile, not an Agent property.

#### Scenario: Agent no longer carries model fields

- **WHEN** the Agent entity is read from the database
- **THEN** it exposes no model_provider, model_id, api_key, api_base_url, or supports_vision fields.

#### Scenario: Two runs of the same agent use different models

- **WHEN** the same Custom adapter agent is run twice with different ModelProfile selections on the messages
- **THEN** each `AgentRun` records the model_id and model_provider of the profile used for that run
- **AND** the Agent entity itself is unchanged.
