# Spec: Memory Extraction

## ADDED Requirements

### Requirement: LTM extraction SHALL process full conversation

The LTM extraction pipeline MUST receive both the user message and the assistant reply as input for each conversation turn. The extraction LLM MUST be fed the complete conversation (user + assistant), not just the assistant reply text. This ensures user-stated facts (e.g., "we use React 19") are captured even when the assistant does not restate them.

#### Scenario: User states a project fact

- **WHEN** the user says "我们项目用 React 19" and the assistant replies "好的，记住了"
- **THEN** the LTM extraction MUST extract a memory like "User's project uses React 19" from the full conversation
- **AND** the memory MUST be stored in LTM with an embedding

#### Scenario: Trivial conversation produces no memories

- **WHEN** the user says "你好" and the assistant replies "你好！有什么可以帮你的？"
- **THEN** the LTM extraction MUST return an empty memory list
- **AND** no new LTM item MUST be created

### Requirement: LTM extraction SHALL output natural language memory strings

The LTM extraction prompt MUST produce self-contained natural language memory statements (e.g., "User's project uses React 19"), not k-v pairs (e.g., `{"技术栈": "React 19"}`). Each memory MUST be understandable without conversation context. The output format MUST be a JSON object with a `memory` array containing objects with `text` and `attributed_to` fields.

#### Scenario: Multi-topic conversation extracts multiple memories

- **WHEN** the user says "我叫张三，我们项目用 React 19，打算下周重构认证模块"
- **THEN** the LTM extraction MUST produce at least 3 separate memory entries
- **AND** each entry MUST be a self-contained natural language statement

### Requirement: LTM extraction SHALL cover broad information types

The LTM extraction prompt MUST instruct the LLM to extract all of the following information types when present: personal preferences, important personal details, plans and intentions, activity preferences, health and wellness, professional details, and miscellaneous information. The prompt MUST NOT restrict extraction to "objective facts" only — it MUST also capture subjective experiences, plans, decisions, and motivations.

#### Scenario: User expresses a plan

- **WHEN** the user says "我打算下周重构认证模块"
- **THEN** the LTM extraction MUST extract a memory capturing the plan and temporal reference
- **AND** the memory MUST NOT be dropped because it is not an "objective fact"

### Requirement: LTM extraction SHALL deduplicate within a single response

The extraction prompt MUST include instructions to avoid within-response duplication: each piece of information MUST appear exactly once in the output. If the user and assistant both mention the same fact, it MUST be extracted once from the original source (typically the user).

#### Scenario: Assistant restates user's fact

- **WHEN** the user says "我用 PostgreSQL" and the assistant replies "好的，你用的是 PostgreSQL"
- **THEN** only one memory about "User uses PostgreSQL" MUST be extracted
- **AND** it MUST be attributed to the user

### Requirement: LTM extraction SHALL skip classification routing

Extracted LTM memories MUST go directly to `LongTerm.store_classified()` without passing through `classify_memory_content()` or `llm_classify_memory()`. The `category` parameter passed to `store_classified` MUST default to an empty string or `"general"`. No memory MUST be dropped based on category classification.

#### Scenario: Identity-type memory enters LTM

- **WHEN** the LTM extraction produces a memory like "User's name is Zhang San"
- **THEN** the memory MUST be stored in LTM via `store_classified` without being redirected to the Preference table
- **AND** the memory MUST participate in future LTM semantic recall

### Requirement: LTM extraction SHALL store attributed_to as a tag

Each extracted memory's `attributed_to` field (value: `"user"` or `"assistant"`) MUST be stored in the `Item.tags` list. This preserves source attribution without requiring DB schema changes.

#### Scenario: User-stated memory tagged as user

- **WHEN** the LLM extracts a memory attributed to "user"
- **THEN** the stored LTM item MUST have `"user"` in its `tags` list

### Requirement: Preference extraction SHALL use broad user-focused prompt

The Preference extraction prompt MUST cover the same 7 information types as the LTM prompt (personal preferences, personal details, plans, activities, health, professional, miscellaneous) but scoped to user messages only. The prompt MUST produce KV pairs (key=value) for the Preference store. The prompt MUST NOT restrict extraction to "personal preferences" alone.

#### Scenario: User shares professional detail

- **WHEN** the user says "我是前端工程师，用 TypeScript"
- **THEN** the Preference extraction MUST extract at least `职业=前端工程师` and `语言=TypeScript` as KV pairs
- **AND** these MUST be stored in the Preference table

### Requirement: Preference extraction SHALL fix existing_keys instruction bug

When `existing_keys` are provided, the Preference extraction function MUST pass the modified system prompt (containing existing key names) to the LLM, not the original constant. The same fix MUST be applied to the LTM extraction function.

#### Scenario: Existing preference key is reused

- **WHEN** the Preference table already has key `姓名=张三` and the user says "我叫张三"
- **THEN** the extraction MUST instruct the LLM to reuse the existing key `姓名`
- **AND** the LLM call MUST receive the instruction containing the existing key list

### Requirement: Conflict between stores SHALL be resolved by prompt hierarchy

When the Preference table (KV) and LTM (natural language) contain overlapping or conflicting identity/preference information, the system MUST NOT perform active synchronization. `ProfileSource` (Preference) MUST be treated as the authoritative layer, injected as `【用户画像】`. `RecallSource` (LTM) MUST be treated as the supplementary layer, injected as `【相关回忆】`. The prompt structure MUST make the authority hierarchy clear.

#### Scenario: Preference updated, LTM has stale narrative

- **WHEN** the Preference table has `姓名=李四` (updated via conversation) and LTM has both "User's name is Zhang San" (old) and "User changed name to Li Si" (new)
- **THEN** the prompt MUST inject `姓名: 李四` under `【用户画像】` (authoritative)
- **AND** both LTM memories MUST appear under `【相关回忆】` (supplementary, providing narrative context)

### Requirement: LTM extraction SHALL run as a single call per run

The LTM extraction MUST be triggered once per agent run, after both user and assistant messages are available. The extraction MUST NOT run separately per message. The extraction MUST run as a background `asyncio.create_task` to avoid blocking the response path.

#### Scenario: Extraction triggered after run completion

- **WHEN** an agent run completes with user prompt "我们项目用 React 19" and assistant reply "好的，我了解了"
- **THEN** exactly one LTM extraction call MUST be made with the full conversation
- **AND** the extraction MUST run in the background without blocking the run response
