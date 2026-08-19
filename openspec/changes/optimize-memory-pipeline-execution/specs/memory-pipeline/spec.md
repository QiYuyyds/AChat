# Memory Pipeline

## MODIFIED Requirements

### Requirement: auto_memory SHALL extract facts from conversations into daily cards with structured LLM output

After a conversation run ends, auto_memory SHALL use an LLM call to extract key facts from the conversation and write them as a daily card under `daily/<date>/<session_event>.md`. The LLM SHALL receive one of two prompt templates depending on whether the daily card already exists:

- **Create path**: No existing daily card for this session. The LLM SHALL generate `{"action": "create", "name": "<semantic-name>", "description": "<one-line summary>", "body": "<markdown body>", "tags": [...], "importance": 0.0-1.0}`.
- **Update path**: An existing daily card for this session is provided to the LLM. The LLM SHALL merge new facts into the existing body following merge rules: timeline events are appended chronologically; state descriptions are rewritten to reflect the latest state; semantic duplicates are skipped. The LLM SHALL output `{"action": "update", "name": "<semantic-name>", "body": "<complete merged body>", "tags": [...], "importance": 0.0-1.0}`.

The LLM prompt SHALL instruct extraction of: decisions and rationale, current state, reusable procedures. The prompt SHALL explicitly instruct **skipping preference-type facts** (user name, hobbies, location, etc.) — those are handled by the separate PG-backed Preference system. Pure greetings or small talk SHALL be skipped.

The program SHALL use the LLM-generated `name` field to determine the filename. If the file already exists (update path), the existing filename is preserved. If creating a new file, the program SHALL sanitize the name (kebab-case, max 50 chars) and append a short hash for uniqueness.

#### Scenario: Substantive conversation creates new daily card
- **WHEN** a conversation run ends with substantive content and no existing daily card
- **THEN** auto_memory calls LLM with the create prompt template
- **AND** the LLM outputs `action="create"` with name, description, body, tags, importance
- **AND** the program writes `daily/<date>/<sanitized-name>.md` with frontmatter and body
- **AND** frontmatter includes session_id, source link, tags, importance, and the LLM-generated name

#### Scenario: Second conversation updates existing daily card
- **WHEN** a conversation run ends and a daily card already exists for this session
- **THEN** auto_memory calls LLM with the update prompt template, providing the existing card body
- **AND** the LLM outputs `action="update"` with a fully merged body
- **AND** the program overwrites the existing file with the merged body
- **AND** frontmatter `updated_at` is refreshed

#### Scenario: Trivial conversation is skipped
- **WHEN** a conversation run ends with only greetings or small talk
- **THEN** auto_memory skips writing and returns without creating a daily card

### Requirement: auto_dream SHALL refine daily cards into digest memory with per-bucket prompts and provenance wikilinks

auto_dream SHALL execute a three-step pipeline: (1) extract — scan **changed** daily files (via file catalog mtime comparison), LLM identifies reusable abstractions and classifies them into procedure/wiki buckets, with cross-file merge guidance; (2) integrate — for each unit, search existing digest via `node_search`, then LLM generates action (CREATE/CORROBORATE/REFINE/CORRECT) + content using **per-bucket prompt** (procedure = runbook body shape, wiki = encyclopedia body shape), with provenance wikilink (`derived_from:: [[daily/<path>]]`) and related-node wikilink weaving; (3) topics — LLM selects top-N interest topics from extract candidates with Topic Quality guidance.

#### Scenario: auto_dream creates new digest node with provenance
- **WHEN** dream_integrate processes a unit with no matching existing digest node
- **THEN** the per-bucket prompt (procedure or wiki) is used to generate the digest body
- **AND** the body includes `derived_from:: [[daily/<date>/<session>.md]]` pointing to the source
- **AND** the body includes wikilinks to related digest nodes
- **AND** a new file is created in `digest/{bucket}/<name>.md`
- **AND** frontmatter includes source, bucket, importance, and derived_from

#### Scenario: auto_dream refines existing digest node
- **WHEN** dream_integrate identifies a unit that matches an existing digest node (via node_search)
- **THEN** the LLM receives the existing digest body and classifies the action as CORROBORATE, REFINE, or CORRECT
- **AND** the LLM generates the updated body with new evidence merged
- **AND** existing `derived_from::` wikilinks are preserved (additive only)
- **AND** new provenance wikilinks are appended

#### Scenario: auto_dream extract only processes changed files
- **WHEN** dream_extract runs and the file catalog reports 3 changed daily files
- **THEN** only those 3 files are sent to the LLM for extraction
- **AND** unchanged files are skipped

#### Scenario: auto_dream extract merges cross-file evidence
- **WHEN** dream_extract identifies the same abstraction from multiple daily files
- **THEN** the extract prompt instructs the LLM to merge evidence from all files into a single unit
- **AND** the unit's source includes all contributing file paths

#### Scenario: auto_dream topics uses LLM selection
- **WHEN** dream_extract produces topic candidates
- **THEN** dream_topics sends candidates + same-day existing + recent 7-day topics to LLM
- **AND** the LLM selects up to N topics (default 3) based on Topic Quality guidance
- **AND** same-day existing topics are preserved (not overwritten)
- **AND** recent 7-day topics are used for deduplication
- **AND** if LLM is unavailable, fallback to pure rule-based selection (top-N fresh)

### Requirement: auto_index SHALL maintain BM25 and wikilink indexes with broken link detection

auto_index SHALL monitor file changes in `daily/` and `digest/` directories and update: (1) SQLite FTS5 BM25 index, (2) wikilink adjacency graph with predicate column, (3) file catalog. Index updates SHALL be triggered after file writes and on service startup (full reindex).

auto_index SHALL detect broken wikilinks: when a wikilink target file does not exist on disk, the adjacency entry SHALL be flagged. During full reindex, all broken wikilink entries SHALL be removed from the adjacency graph.

#### Scenario: New daily card triggers index update
- **WHEN** a new file is written under `daily/`
- **THEN** auto_index adds the file content to the FTS5 index
- **AND** auto_index extracts and registers any wikilinks (including predicate syntax)
- **AND** the file catalog is updated with the new path and st_mtime

#### Scenario: Service startup performs full reindex
- **WHEN** the memory service starts
- **THEN** a full reindex of all `daily/` and `digest/` files is performed
- **AND** BM25 and wikilink indexes are rebuilt from scratch
- **AND** broken wikilinks (target file missing) are removed

#### Scenario: Broken wikilink is detected and cleaned
- **WHEN** auto_index processes a file containing `[[digest/procedure/old-name.md]]`
- **AND** `digest/procedure/old-name.md` does not exist on disk
- **THEN** the wikilink adjacency entry is flagged as broken
- **AND** during full reindex, the broken entry is removed

### Requirement: proactive SHALL surface interest topics to agents

Before an agent decides to act, proactive SHALL read `daily/<date>/interests.yaml` and return structured topics. The host agent decides whether and how to mention them.

#### Scenario: proactive returns topics
- **WHEN** `proactive()` is called and interests.yaml exists for today
- **THEN** a list of topics with title, reason, keywords, and evidence paths is returned
- **AND** the host agent incorporates relevant topics into its context
