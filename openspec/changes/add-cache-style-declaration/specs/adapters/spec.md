# Adapters

## MODIFIED Requirements

### Requirement: Adapters SHALL translate provider output to StreamEvent

Each adapter MUST expose `stream(input, signal)` and yield only AChat `StreamEvent` objects to the application layer. Each adapter MUST resolve and set the `cacheStyle` field on every `RunUsage` it emits, based on the provider/model identity at run start.

#### Scenario: Custom model emits tool calls

- **WHEN** Chat Completions streaming returns function tool call deltas
- **THEN** CustomAgentAdapter accumulates arguments
- **AND** emits AChat `tool.call` and `tool.result` events.

#### Scenario: Custom adapter resolves cacheStyle for known provider

- **WHEN** a Custom adapter agent runs with a ModelProfile whose provider is `deepseek`
- **THEN** the adapter sets `cacheStyle='deepseek'` on the `RunUsage` payload
- **AND** does not consult `ModelProfile.cache_style` or `detected_cache_style` (hardcoded for known providers).

#### Scenario: Custom adapter resolves cacheStyle for openai-compatible (user-declared)

- **WHEN** a Custom adapter agent runs with an `openai-compatible` ModelProfile where `cache_style='anthropic'`
- **THEN** the adapter sets `cacheStyle='anthropic'` on the `RunUsage` payload
- **AND** does not perform auto-detection (user declaration takes priority).

#### Scenario: Custom adapter resolves cacheStyle for openai-compatible (auto-detect, cached)

- **WHEN** a Custom adapter agent runs with an `openai-compatible` ModelProfile where `cache_style IS NULL` and `detected_cache_style='deepseek'`
- **THEN** the adapter sets `cacheStyle='deepseek'` on the `RunUsage` payload
- **AND** does not perform re-detection (reuses cached detection result).

#### Scenario: Custom adapter auto-detects cacheStyle from LLM response

- **WHEN** a Custom adapter agent runs with an `openai-compatible` ModelProfile where both `cache_style` and `detected_cache_style` are NULL
- **AND** the first LLM response contains `cache_creation_input_tokens` in usage
- **THEN** the adapter sets `cacheStyle='anthropic'` on the `RunUsage` payload
- **AND** persists `detected_cache_style='anthropic'` on the ModelProfile for future runs.

#### Scenario: Custom adapter auto-detects no-cache model

- **WHEN** a Custom adapter agent runs with an `openai-compatible` ModelProfile where both `cache_style` and `detected_cache_style` are NULL
- **AND** the LLM response usage object contains no cache-related fields
- **THEN** the adapter sets `cacheStyle='none'` on the `RunUsage` payload
- **AND** persists `detected_cache_style='none'` on the ModelProfile.

#### Scenario: Claude adapter hardcodes anthropic cacheStyle

- **WHEN** a Claude adapter agent run emits `run.usage`
- **THEN** the payload includes `cacheStyle='anthropic'`
- **AND** the adapter does not consult ModelProfile cache fields.

#### Scenario: Custom adapter resolves cacheStyle fallback

- **WHEN** a Custom adapter agent runs with an `openai-compatible` ModelProfile where both `cache_style` and `detected_cache_style` are NULL
- **AND** the first LLM response has no usage object (or usage with no cache fields)
- **THEN** the adapter sets `cacheStyle='deepseek'` (conservative default) on the `RunUsage` payload
- **AND** does NOT persist `detected_cache_style` (will retry detection on next run).
