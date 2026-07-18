# Stream Events

## MODIFIED Requirements

### Requirement: Usage events SHALL update durable accounting

Adapters SHALL emit `message.usage` and `run.usage` when provider usage data is available, and AgentRunner MUST persist those payloads without coupling to provider-specific token fields.

The `RunUsage` payload SHALL carry both accumulated totals (across all turns in the run) and last-turn snapshots so the frontend can distinguish cumulative billing from single-turn context window occupancy:

- **Accumulated fields** (existing, unchanged): `inputTokens`, `outputTokens`, `cacheCreationTokens`, `cacheReadTokens`
- **Last-turn snapshot fields**:
  - `lastInputTokens` (existing): prompt token count of the final turn. For DeepSeek-style providers where `prompt_tokens` already includes cache hit, this value includes cache read. For Anthropic-style providers where `input_tokens` excludes cache, this value excludes cache read and cache creation.
  - `lastCacheReadTokens` (new, optional, default 0): cache hit tokens of the final turn. Enables the frontend to decompose `lastInputTokens` into cache-reused vs net-new content for the context window row.
  - `lastOutputTokens` (new, optional, default 0): output tokens of the final turn.
- **Run metadata**:
  - `turnCount` (new, optional, default 0): number of model calls in this run (ReAct turns including forced final). Enables the frontend to label cumulative values as "across N turns".
  - `model` (existing): effective model id.

All new fields are optional with default 0 for backward compatibility. Persisted `agent_runs.usage` JSON from older runs will lack these fields; consumers MUST treat missing fields as 0.

#### Scenario: Codex reports turn usage
- **WHEN** Codex emits `turn.completed.usage`
- **THEN** the adapter emits `message.usage`
- **AND** the adapter emits `run.usage` with the effective model id.

#### Scenario: Multi-turn ReAct run reports last-turn snapshot
- **WHEN** an AgentRunner ReAct loop completes after N turns
- **THEN** `run.usage` carries `turnCount=N`
- **AND** `lastInputTokens` / `lastCacheReadTokens` / `lastOutputTokens` reflect the final turn's values (not the peak or average).

#### Scenario: Old run JSON lacks new fields
- **WHEN** the frontend loads a `agent_runs.usage` JSON persisted before this change
- **THEN** missing `lastCacheReadTokens` / `lastOutputTokens` / `turnCount` are treated as 0
- **AND** the UI degrades gracefully (no crash, context window row shows total without decomposition).

#### Scenario: Mid-run compact changes last-turn values
- **WHEN** a mid-run compaction reduces the message list and the next turn's `prompt_tokens` drops
- **THEN** `lastInputTokens` reflects the post-compact turn (smaller), not the pre-compact peak
- **AND** the frontend displays the post-compact value as "current ctx" (the peak is not surfaced by this change).
