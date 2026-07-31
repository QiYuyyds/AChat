# Spec Delta: conversation-context

## MODIFIED Requirements

### Requirement: Custom agents SHALL receive bounded chat history

CustomAgentAdapter runs MUST receive serialized conversation history within a model-aware token budget for ordinary user turns. The token budget SHALL be calculated using the **effective context window** (`min(context_window, EFFECTIVE_CONTEXT_CAP)`) rather than the physical context window, to avoid quality degradation (Lost in the Middle, Context Rot) at excessive context lengths. The effective context cap SHALL be 200,000 tokens.

#### Scenario: DeepSeek agent with 1M physical context window
- **WHEN** AgentRunner builds adapter input for a DeepSeek agent (physical context = 1,000,000)
- **THEN** it uses effective_context_window = 200,000 to compute history_budget and model_context_limit
- **AND** pre-run pruning triggers at ~130K tokens (0.65 × 200K) instead of ~650K (0.65 × 1M)

#### Scenario: Conversation has long history
- **WHEN** AgentRunner builds adapter input
- **THEN** it trims history to fit the effective context window and output reserve.

#### Scenario: Agent with physical context window below cap
- **WHEN** a model has physical context_window ≤ 200,000 (e.g. Claude 200K, GPT-4o 128K)
- **THEN** effective_context_window equals the physical context_window (no change in behavior)
