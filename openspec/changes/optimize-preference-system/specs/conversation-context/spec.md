## ADDED Requirements

### Requirement: Profile slot SHALL be dynamic for cache stability

The SlotProfile in all 4 built-in Schemas (CHAT, TOOL, REACT, RAG) MUST be marked `static=False`, so that Profile content is rendered via `render_dynamic()` and injected into the user message prefix wrapped in `<system-reminder>` tags. The system prompt MUST contain only `static=True` slots (Constraints), maximizing prompt cache prefix stability.

#### Scenario: System prompt excludes Profile content
- **WHEN** AgentRunner builds the system prompt via `ctx.render_static()`
- **THEN** only Constraints slots appear in the system prompt; Profile content is excluded.

#### Scenario: Profile content injected as user message prefix
- **WHEN** AgentRunner builds the effective prompt via `ctx.render_dynamic()`
- **THEN** Profile content is wrapped in `<system-reminder>` tags and prepended to the user message.

### Requirement: ProfileSource SHALL read only from Preference table

ProfileSource MUST NOT accept or use an `ltm` parameter. It SHALL read exclusively from the `preference_provider` (Preference store), since memory_writer routes identity/preference facts only to the Preference table (single-write mode). The LTM `filter_by_category` path for identity/preference is removed.

#### Scenario: ProfileSource registered without LTM
- **WHEN** `ProfileSource` is instantiated in `main.py`
- **THEN** only `preference_provider` is passed; no `ltm` argument is provided.

#### Scenario: No LTM identity/preference data fetched
- **WHEN** `ProfileSource.fetch()` is called
- **THEN** it returns items only from `preference_provider.get_all()`; no `filter_by_category` call is made.

### Requirement: ProfileSource SHALL compute score dynamically

ProfileSource MUST compute a score for each Preference item by calling `classify_memory_content(key, value)` to determine the category, then mapping via `_IMPORTANCE_BY_CATEGORY` (identity=0.9, preference=0.7, general=0.3). Items MUST be sorted by score descending before token_budget trimming.

#### Scenario: Identity key gets high score
- **WHEN** a Preference item has key "姓名" and value "张三"
- **THEN** `classify_memory_content("姓名", "张三")` returns category "identity", and the item receives score 0.9.

#### Scenario: Unclassified key gets default score
- **WHEN** a Preference item has key "天气" and value "晴" (no rule match)
- **THEN** the item receives score 0.3 (general fallback).

### Requirement: Profile slot SHALL use token_budget only, not top_k

All 4 built-in Schemas MUST NOT set `top_k` on the SlotProfile filter. Trimming SHALL be done solely by `token_budget` via `_trim_by_budget`, after sorting items by score descending. This prevents key-count-induced truncation shifts that destabilize the rendered content.

#### Scenario: Many preferences within budget
- **WHEN** 12 Preference items exist and total tokens are within `token_budget=600`
- **THEN** all 12 items are included in the rendered output, sorted by score descending.

#### Scenario: Preferences exceed budget
- **WHEN** 15 Preference items exist and total tokens exceed `token_budget=600`
- **THEN** items are sorted by score descending and `_trim_by_budget` drops the lowest-score items until the budget is met; high-score items (identity, preference) are retained.
