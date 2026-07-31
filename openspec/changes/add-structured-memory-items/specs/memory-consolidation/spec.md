## ADDED Requirements

### Requirement: Consolidation merge SHALL sync summary, keywords, and content_scope

When consolidation merges two items (similarity ≥ merge threshold), the surviving item SHALL inherit the structured fields from both items: `summary` SHALL prefer the non-empty value with more information; `keywords` SHALL be the deduplicated union (capped at 8); `content_scope` SHALL prefer the non-empty value.

#### Scenario: Merge combines keywords from both items

- **WHEN** item A has `keywords=["TypeScript", "React"]` and item B has `keywords=["Next.js", "React"]` are merged
- **THEN** the surviving item SHALL have `keywords=["TypeScript", "React", "Next.js"]` (deduplicated)
- **AND** the keyword list SHALL be capped at 8 entries

#### Scenario: Merge preserves non-empty summary

- **WHEN** item A has `summary="前端技术栈"` and item B has `summary=""` are merged
- **THEN** the surviving item SHALL have `summary="前端技术栈"`

#### Scenario: Merge prefers non-empty content_scope

- **WHEN** item A has `content_scope=""` and item B has `content_scope="d:/project/agenthub"` are merged
- **THEN** the surviving item SHALL have `content_scope="d:/project/agenthub"`

### Requirement: Consolidation SHALL apply category-specific lifecycle parameters

`ConsolidationConfig` SHALL include case-specific lifecycle parameters: `case_ttl_days` (default 90), `case_decay_rate` (default 0.998), `case_min_importance` (default 0.4), `case_dedup_threshold` (default 0.90). During consolidation, items with `category="case"` SHALL use the case-specific parameters instead of the default parameters. Items with other categories SHALL use the default parameters.

#### Scenario: Case memory has longer TTL

- **WHEN** consolidation evaluates a case memory created 60 days ago
- **THEN** the item SHALL NOT be expired (case TTL = 90 days)
- **AND** a non-case memory created 60 days ago SHALL be expired (default TTL = 30 days)

#### Scenario: Case memory decays slower

- **WHEN** consolidation decays a case memory with current importance 0.60 over 10 days
- **THEN** the decayed importance SHALL use `case_decay_rate=0.998`
- **AND** the resulting importance SHALL be higher than if the default `decay_rate=0.995` were applied

#### Scenario: Case memory has higher minimum importance floor

- **WHEN** consolidation evaluates a case memory whose importance has decayed to 0.35
- **THEN** the item SHALL NOT be expired (case `min_importance=0.4`, current 0.35 < 0.4)
- **AND** the item SHALL be expired because its importance is below the case minimum

#### Scenario: Case memories use case dedup threshold

- **WHEN** consolidation checks two case memories with summary embedding similarity 0.92
- **THEN** they SHALL be evaluated against `case_dedup_threshold=0.90`
- **AND** since 0.92 ≥ 0.90, they SHALL be deduped

#### Scenario: Non-case memories use default dedup threshold

- **WHEN** consolidation checks two fact memories with summary embedding similarity 0.92
- **THEN** they SHALL be evaluated against `dedup_threshold=0.95`
- **AND** since 0.92 < 0.95, they SHALL NOT be deduped
