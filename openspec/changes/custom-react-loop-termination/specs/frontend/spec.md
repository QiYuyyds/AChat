## ADDED Requirements

### Requirement: Frontend SHALL show light stop hints for abnormal Custom terminations

When run completion metadata includes a user-facing `stopReasonLabel` (or equivalent) for a non-natural completion, the frontend MUST show a lightweight Chinese hint near the run/message UI. The frontend MUST NOT render soft wrap-up injection content as a normal chat message.

#### Scenario: Context-limit summary hint
- **WHEN** a run completes with a label indicating automatic summary due to context/budget limits
- **THEN** the UI displays that short hint to the user
- **AND** still shows the assistant’s final natural-language summary text

#### Scenario: Soft wrap-up inject not shown
- **WHEN** the backend injects a hidden soft wrap-up instruction for the model
- **THEN** the chat message list MUST NOT display that instruction as a user or system bubble in the default UI

#### Scenario: Natural complete stays quiet
- **WHEN** a run ends with model-done completion and no abnormal stop label
- **THEN** the UI MUST NOT show an error-style stop hint solely because the run finished
