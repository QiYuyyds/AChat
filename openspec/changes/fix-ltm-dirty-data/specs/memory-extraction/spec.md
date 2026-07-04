## MODIFIED Requirements

### Requirement: User messages SHALL NOT be stored verbatim into long-term memory

The system SHALL NOT store raw user message text into LTM. `on_message_end(role="user")` SHALL only drive preference extraction; LTM ingestion happens exclusively through classified extraction (see the routing requirement below). This replaces the prior behavior where every user message was appended to LTM via `_safe_ltm_add`.

#### Scenario: Conversational filler produces no LTM row
- **WHEN** the user sends `继续` / `好的` / `帮我改一下`
- **THEN** no row is inserted into `long_term_memory`
- **AND** short-term memory and `chat_history` are still updated as before

#### Scenario: User identity still captured via preference chain
- **WHEN** the user sends `我叫张三`
- **THEN** `姓名=张三` is written to the preference store
- **AND** no corresponding raw row is inserted into `long_term_memory`

### Requirement: Extracted facts SHALL be routed to a single store by category

`extract_memory_from_reply` SHALL route each extracted key-value fact to exactly one store based on its classified `category`, eliminating the previous unconditional double-write to both the preference store and LTM.

Routing table:
- `identity`, `preference` → preference store only (`preference.set`)
- `fact`, `episodic`, `policy`, `tool_failure` → LTM only (`ltm.store_classified`)
- `general` → discarded

#### Scenario: Preference-class fact goes only to preference store
- **WHEN** extraction yields `{"喜好": "Python"}` classified as `preference`
- **THEN** `preference.set("喜好", "Python")` is called
- **AND** `ltm.store_classified` is NOT called for this fact

#### Scenario: Fact-class fact goes only to LTM
- **WHEN** extraction yields `{"项目数据库": "PostgreSQL"}` classified as `fact`
- **THEN** `ltm.store_classified(...)` is called with category `fact`
- **AND** `preference.set` is NOT called for this fact

#### Scenario: General-class fact is dropped
- **WHEN** extraction yields a fact classified as `general`
- **THEN** neither the preference store nor LTM receives a write for it

#### Scenario: No fact survives in two stores
- **WHEN** any single extracted key-value fact is processed
- **THEN** it results in at most one persisted record across the preference store and LTM combined

### Requirement: LTM fact content SHALL NOT carry a hardcoded "用户" prefix

Facts stored into LTM (fact/episodic/policy/tool_failure) are objective world facts, not user-profile attributes. Their content SHALL be rendered as a plain `"<key>: <value>"` without prepending "用户". A leading "用户" the LLM includes in the key SHALL be stripped, not doubled. User-profile facts (identity/preference) are unaffected because they route to the preference store with the raw key.

#### Scenario: World fact is not mislabeled as a user attribute
- **WHEN** a weather fact `{"气温": "15°C ~ 27°C"}` is classified as `fact`
- **THEN** the stored LTM content is `"气温: 15°C ~ 27°C"`
- **AND** it is NOT `"用户气温: 15°C ~ 27°C"`

#### Scenario: Leading 用户 in the key is stripped once
- **WHEN** the extracted key is `"用户数据库"` with value `"PostgreSQL"`, classified as `fact`
- **THEN** the stored LTM content is `"数据库: PostgreSQL"`

### Requirement: User-message preference extraction SHALL run a single pass

For a user message, when an LLM `generate_fn` is available the system SHALL run only the LLM preference extraction; the rule-based pass SHALL run only as a fallback when no `generate_fn` is configured. Running both produces duplicate, non-reconciling keys (e.g. `喜好` vs `喜欢的音乐类型`).

#### Scenario: LLM available — rule pass skipped
- **WHEN** `on_message_end("user", msg)` runs with `generate_fn` set
- **THEN** LLM preference extraction is dispatched
- **AND** the rule-based extraction pass is NOT run

#### Scenario: No LLM — rule fallback
- **WHEN** `on_message_end("user", msg)` runs with no `generate_fn`
- **THEN** the rule-based extraction pass runs

### Requirement: Preference extraction SHALL NOT infer identity or transient subjects

Preference extraction SHALL only record the user's own name when explicitly stated ("我叫X"/"我的名字是X"), SHALL NOT treat names of people/works the user merely likes as the user's identity, and SHALL NOT record objects the user only queried about as user preferences.

#### Scenario: Liking a celebrity does not set the user's name
- **WHEN** the user says "我喜欢周润发"
- **THEN** `姓名` is NOT set to "周润发"
- **AND** at most a preference like `偶像`/`喜好` is recorded

#### Scenario: Querying a city's weather is not a location preference
- **WHEN** the user asks "北京今天天气"
- **THEN** no `地点`/location preference is recorded for the user

### Requirement: LTM facts SHALL be self-contained

Facts extracted into LTM SHALL carry their subject/entity/time in the key so they are meaningful out of conversational context. Context-stripped isolated values SHALL be avoided.

#### Scenario: Weather fact keeps its city
- **WHEN** the assistant reports Beijing weather with humidity ~71%
- **THEN** the stored fact is subject-qualified, e.g. `"北京湿度(<date>): 约71%"`
- **AND** NOT a bare `"湿度: 约71%"` that collides with other cities
