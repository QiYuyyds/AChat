# Stream Events

## MODIFIED Requirements

### Requirement: Usage events SHALL update durable accounting

Adapters SHALL emit `message.usage` and `run.usage` when provider usage data is available, and AgentRunner MUST persist those payloads without coupling to provider-specific token fields.

The `RunUsage` payload SHALL carry a `cacheStyle` field (`'deepseek'` | `'anthropic'` | `'none'`) that declares the cache semantics of the tokens in this run. Adapters MUST set `cacheStyle` based on the resolved provider/model at run start, not infer it from usage data. This field replaces the legacy `cacheCreationTokens > 0` inference signal used by the frontend.

The `RunUsage` payload SHALL carry both accumulated totals (across all turns in the run) and last-turn snapshots so the frontend can distinguish cumulative billing from single-turn context window occupancy:

- **Accumulated fields** (existing, unchanged): `inputTokens`, `outputTokens`, `cacheCreationTokens`, `cacheReadTokens`
- **Last-turn snapshot fields** (existing, unchanged): `lastInputTokens`, `lastCacheReadTokens`, `lastOutputTokens`
- **Run metadata** (existing, unchanged): `turnCount`, `model`
- **Cache semantics** (new): `cacheStyle` — declares how `inputTokens` and `cacheReadTokens` relate for this run

The `MessageUsage` payload MAY carry an optional `cacheStyle` field. When present, it declares the cache semantics for that single message's usage. When absent, the frontend SHALL infer from the associated run's `cacheStyle`, or fall back to `'deepseek'` if no run context is available.

All new fields are optional with sensible defaults for backward compatibility. Persisted `agent_runs.usage` JSON from older runs will lack `cacheStyle`; consumers MUST infer it via `cacheCreationTokens > 0 ? 'anthropic' : 'deepseek'` (the legacy inference, matching pre-change behavior).

#### Scenario: DeepSeek run reports cacheStyle

- **WHEN** a DeepSeek Custom adapter agent run emits `run.usage`
- **THEN** the payload includes `cacheStyle: 'deepseek'`
- **AND** `cacheCreationTokens` is 0
- **AND** `inputTokens` includes `cacheReadTokens` (DeepSeek's `prompt_tokens` includes `prompt_cache_hit_tokens`)

#### Scenario: Anthropic run reports cacheStyle

- **WHEN** a Claude adapter agent run emits `run.usage`
- **THEN** the payload includes `cacheStyle: 'anthropic'`
- **AND** `cacheCreationTokens` may be > 0
- **AND** `inputTokens` excludes cache read and cache creation

#### Scenario: openai-compatible run with auto-detected cacheStyle

- **WHEN** a Custom adapter agent run uses an `openai-compatible` ModelProfile with no user-declared `cacheStyle`
- **AND** the LLM response contains `prompt_cache_hit_tokens` or `cached_tokens` in usage
- **THEN** the adapter sets `cacheStyle: 'deepseek'` in the `run.usage` payload
- **AND** the adapter persists `detected_cache_style = 'deepseek'` on the ModelProfile

#### Scenario: openai-compatible run with no cache fields

- **WHEN** a Custom adapter agent run uses an `openai-compatible` ModelProfile with no user-declared `cacheStyle`
- **AND** the LLM response contains no cache-related fields in usage
- **THEN** the adapter sets `cacheStyle: 'none'` in the `run.usage` payload
- **AND** the adapter persists `detected_cache_style = 'none'` on the ModelProfile

#### Scenario: Legacy run without cacheStyle

- **WHEN** the frontend processes a `run.usage` payload from an older run that lacks the `cacheStyle` field
- **THEN** the frontend infers `cacheStyle` via `cacheCreationTokens > 0 ? 'anthropic' : 'deepseek'`
- **AND** the behavior is identical to the pre-change inference logic

#### Scenario: Codex reports turn usage

- **WHEN** Codex emits `turn.completed.usage`
- **THEN** the adapter emits `message.usage`
- **AND** the adapter emits `run.usage` with the effective model id and resolved `cacheStyle`.

#### Scenario: Multi-turn ReAct run reports last-turn snapshot

- **WHEN** an AgentRunner ReAct loop completes after N turns
- **THEN** `run.usage` carries `turnCount = N`
- **AND** `lastInputTokens` / `lastCacheReadTokens` / `lastOutputTokens` snapshot the final turn
- **AND** `cacheStyle` is the resolved style for the entire run.
