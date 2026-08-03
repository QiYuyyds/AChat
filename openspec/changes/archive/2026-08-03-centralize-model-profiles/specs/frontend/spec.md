## MODIFIED Requirements

### Requirement: Message input bar SHALL provide a model selector for SDK agent conversations

The conversation input bar SHALL display a model selector dropdown listing the user's ModelProfiles (name + provider + model_id) when the active conversation includes at least one SDK (Custom adapter) agent. The selector SHALL be hidden for conversations whose only agents are CLI adapters. Selecting a profile attaches `modelProfileId` to the outgoing message (plan B: per-message selection). When no profile is selected, the default ModelProfile is used at run time.

#### Scenario: Solo SDK conversation shows model selector

- **WHEN** the active conversation has a Custom adapter agent
- **THEN** the input bar shows a model selector dropdown with the user's ModelProfiles
- **AND** the currently-selected profile (or "default") is indicated.

#### Scenario: CLI-only conversation hides model selector

- **WHEN** the active conversation's only agents are Claude Code or Codex (CLI adapters)
- **THEN** the model selector is hidden.

#### Scenario: User switches model mid-conversation

- **WHEN** the user selects a different ModelProfile and sends a message
- **THEN** the message carries the new `modelProfileId`
- **AND** the run uses the newly-selected model.

#### Scenario: User has zero ModelProfiles

- **WHEN** the user opens a conversation with an SDK agent and has zero ModelProfiles
- **THEN** the model selector shows an empty state with a call-to-action to configure a model in the Model tab
- **AND** sending is disabled until at least one profile exists.

### Requirement: A Model tab SHALL provide ModelProfile CRUD and connectivity testing

A standalone "Model" tab SHALL list all of the user's ModelProfiles with their test status, and provide create/edit/delete operations plus a connectivity test button per profile. The tab SHALL allow marking a profile as default.

#### Scenario: User creates a profile from the Model tab

- **WHEN** the user fills name, provider, model_id, api_key, api_base_url and saves
- **THEN** the profile appears in the list with `untested` status.

#### Scenario: User tests a profile's connectivity

- **WHEN** the user clicks the test button on a profile
- **THEN** a minimal chat completion ping is sent and the result (ok/fail + latency) is shown inline
- **AND** `last_test_status` is updated in the list.
