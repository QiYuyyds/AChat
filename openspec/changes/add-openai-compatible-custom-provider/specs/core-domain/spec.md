### Requirement: Custom agents SHALL support a generic OpenAI-compatible provider

AgentHub SHALL allow `modelProvider='openai-compatible'` for agents whose `adapterName='custom'`.

#### Scenario: Generic provider agent is saved

- **WHEN** a Custom agent is configured with provider `openai-compatible`
- **THEN** the agent persists that provider value
- **AND** it stores the per-agent `apiBaseUrl` used by the Chat Completions endpoint.
