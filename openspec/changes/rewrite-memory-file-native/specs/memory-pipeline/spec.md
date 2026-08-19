# Memory Pipeline

> 实现细节不明确时，查阅 ReMe 源码 `待融合项目/ReMe/reme/steps/evolve/` 目录。

## ADDED Requirements

### Requirement: auto_memory SHALL extract facts from conversations into daily cards

After a conversation run ends, auto_memory SHALL use an LLM call to extract key facts from the conversation and write them as a daily card under `daily/<date>/<session_event>.md`. The LLM prompt SHALL instruct extraction of: decisions and rationale, current state, reusable procedures. The prompt SHALL explicitly instruct **skipping preference-type facts** (user name, hobbies, location, etc.) — those are handled by the separate PG-backed Preference system. Pure greetings or small talk SHALL be skipped.

#### Scenario: Substantive conversation produces daily card
- **WHEN** a conversation run ends with substantive content
- **THEN** auto_memory calls LLM with the conversation transcript and the auto_memory prompt template
- **AND** the LLM output is parsed and written to `daily/<date>/<name>.md`
- **AND** frontmatter includes session_id, source link, and appropriate tags

#### Scenario: Trivial conversation is skipped
- **WHEN** a conversation run ends with only greetings or small talk
- **THEN** auto_memory skips writing and returns without creating a daily card

### Requirement: auto_index SHALL maintain BM25 and wikilink indexes

auto_index SHALL monitor file changes in `daily/` and `digest/` directories and update: (1) SQLite FTS5 BM25 index, (2) wikilink adjacency graph, (3) file catalog. Index updates SHALL be triggered after file writes and on service startup (full reindex).

#### Scenario: New daily card triggers index update
- **WHEN** a new file is written under `daily/`
- **THEN** auto_index adds the file content to the FTS5 index
- **AND** auto_index extracts and registers any wikilinks in the file

#### Scenario: Service startup performs full reindex
- **WHEN** the memory service starts
- **THEN** a full reindex of all `daily/` and `digest/` files is performed
- **AND** BM25 and wikilink indexes are rebuilt from scratch

### Requirement: auto_dream SHALL refine daily cards into digest memory

auto_dream SHALL execute a three-step pipeline: (1) extract — scan changed daily files, LLM identifies reusable abstractions and classifies them into procedure/wiki buckets; (2) integrate — for each unit, search existing digest for dedup, then CREATE/CORROBORATE/REFINE/CORRECT into `digest/{bucket}/`; (3) topics — select top-N interest topics, deduplicate against recent 7 days, write `daily/<date>/interests.yaml`.

#### Scenario: auto_dream creates new digest node
- **WHEN** dream_extract identifies a reusable abstraction with no matching existing digest
- **THEN** dream_integrate creates a new file in `digest/{bucket}/<name>.md`
- **AND** frontmatter includes source, bucket, and importance

#### Scenario: auto_dream refines existing digest node
- **WHEN** dream_extract identifies an abstraction that matches an existing digest node
- **THEN** dream_integrate updates the existing file (REFINE or CORROBORATE action)
- **AND** existing wikilinks and derived_from entries are preserved (additive only)

#### Scenario: auto_dream generates interest topics
- **WHEN** dream_extract produces topic candidates
- **THEN** dream_topics selects up to N topics (default 3)
- **AND** topics are deduplicated against the past 7 days of interests.yaml
- **AND** topics are written to `daily/<date>/interests.yaml`

### Requirement: auto_dream SHALL trigger on threshold and cron

auto_dream SHALL trigger when the count of unprocessed daily cards reaches a threshold (default 5). Additionally, a cron job at 23:00 daily SHALL trigger auto_dream as a fallback.

#### Scenario: Threshold trigger
- **WHEN** 5 or more daily cards exist without corresponding digest entries
- **THEN** auto_dream pipeline is triggered immediately

#### Scenario: Cron fallback
- **WHEN** the daily cron time (23:00) is reached
- **THEN** auto_dream pipeline is triggered regardless of card count

### Requirement: proactive SHALL surface interest topics to agents

Before an agent decides to act, proactive SHALL read `daily/<date>/interests.yaml` and return structured topics. The host agent decides whether and how to mention them.

#### Scenario: proactive returns topics
- **WHEN** `proactive()` is called and interests.yaml exists for today
- **THEN** a list of topics with title, reason, keywords, and evidence paths is returned
- **AND** the host agent incorporates relevant topics into its context
