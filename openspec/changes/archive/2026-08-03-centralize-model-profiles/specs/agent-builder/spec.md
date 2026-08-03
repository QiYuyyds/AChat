## MODIFIED Requirements

### Requirement: Agent create/edit SHALL NOT require model configuration

Creating or editing an Agent SHALL NOT require or accept `model_provider`, `model_id`, `api_key`, or `api_base_url`. The Custom adapter validation that previously required these fields is removed. An Agent is a persona definition (system_prompt, tools, capabilities, adapter_name) only; model selection happens at runtime via ModelProfile.

#### Scenario: Create Custom agent without model fields

- **WHEN** a user creates a Custom adapter agent without providing model_provider or model_id
- **THEN** the agent is created successfully with no model fields stored
- **AND** the agent uses ModelProfile-based resolution at run time.

#### Scenario: Edit existing agent no longer shows model fields

- **WHEN** a user edits an existing agent
- **THEN** the model_provider, model_id, api_key, and api_base_url fields are not present in the edit form
- **AND** saving the agent does not touch any model configuration.

## REMOVED Requirements

### Requirement: Custom adapter create SHALL validate modelProvider and modelId

**Reason**: Model configuration is decoupled from the Agent entity. The `agents.py` create endpoint no longer enforces `if body.adapter_name == "custom" and not (body.model_provider and body.model_id): 400`, nor validates `api_key` / `api_base_url` per-provider.

**Migration**: Model validation moves to ModelProfile CRUD (the Model tab). Existing agents' model fields are migrated to ModelProfiles before the Agent columns are dropped.
