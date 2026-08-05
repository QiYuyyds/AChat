# File-native Memory

## ADDED Requirements

### Requirement: Wikilinks SHALL support predicate syntax

Wikilinks in memory Markdown files SHALL support predicate syntax: `predicate:: [[path]]` (e.g., `derived_from:: [[daily/2026-08-04/sess.md]]`, `relates_to:: [[digest/procedure/react-patterns.md]]`). Plain wikilinks `[[path]]` without a predicate SHALL remain valid and be stored with `predicate = NULL`. The wikilink parser SHALL use the regex pattern `^(\w+)::\s*\[\[([^\]]+)\]\]` to match predicate wikilinks.

The wikilink adjacency graph SHALL store a `predicate TEXT` column. Predicates are free-form strings but the system SHALL recognize a standard vocabulary: `derived_from` (provenance), `relates_to` (general association), `depends_on` (dependency), `contradicts` (conflict).

#### Scenario: Predicate wikilink is parsed and stored
- **WHEN** auto_index processes a file containing `derived_from:: [[daily/2026-08-04/sess.md]]`
- **THEN** the wikilink adjacency graph stores `(source=<current_file>, target="daily/2026-08-04/sess.md", predicate="derived_from")`
- **AND** the predicate is queryable for provenance tracing

#### Scenario: Plain wikilink is stored with NULL predicate
- **WHEN** auto_index processes a file containing `[[digest/procedure/react-patterns.md]]`
- **THEN** the wikilink adjacency graph stores `(source=<current_file>, target="digest/procedure/react-patterns.md", predicate=NULL)`

#### Scenario: Multiple predicates in the same file
- **WHEN** auto_index processes a file containing both `derived_from:: [[daily/2026-08-04/sess.md]]` and `relates_to:: [[digest/wiki/react.md]]`
- **THEN** two adjacency entries are created, each with its respective predicate

### Requirement: Memory files SHALL support move with wikilink retargeting

A `move_file(src_path, dst_path, retarget=True)` operation SHALL move a memory file from `src_path` to `dst_path` and rewrite all wikilinks across the memory workspace that point to `src_path` to point to `dst_path`. The retarget scan SHALL cover all files in `daily/` and `digest/`. After retargeting, the file catalog and BM25 index SHALL be updated for the moved file and all files whose wikilinks were rewritten.

#### Scenario: Renaming a digest file retargets inbound wikilinks
- **WHEN** `move_file("digest/procedure/old-name.md", "digest/procedure/new-name.md")` is called
- **THEN** the file is moved to the new path
- **AND** all files containing `[[digest/procedure/old-name.md]]` are updated to `[[digest/procedure/new-name.md]]`
- **AND** predicate wikilinks (e.g., `derived_from:: [[digest/procedure/old-name.md]]`) are also retargeted
- **AND** the file catalog and BM25 index are updated for all affected files

#### Scenario: Move API is exposed via REST
- **WHEN** `POST /api/memory/move` is called with `{"src": "digest/procedure/old.md", "dst": "digest/procedure/new.md"}`
- **THEN** the move + retarget operation is executed
- **AND** a success response with the new path is returned
- **AND** the operation is scoped to the calling user's memory workspace

### Requirement: Digest frontmatter SHALL preserve status field for lifecycle tracking

Digest memory files SHALL include an optional `status` field in frontmatter with values `active` (default) or `archived`. Archived nodes SHALL remain searchable but SHALL be deprioritized in search results (lower BM25 weight multiplier). auto_dream SHALL NOT update archived nodes.

#### Scenario: Active node is prioritized in search
- **WHEN** a search returns both active and archived nodes
- **THEN** active nodes appear before archived nodes in the result list
- **AND** archived nodes have a lower BM25 score multiplier (0.5x)

#### Scenario: auto_dream skips archived nodes
- **WHEN** dream_integrate finds a matching digest node with `status: archived`
- **THEN** the node is not updated (REFINE/CORRECT skipped)
- **AND** a new active node may be created instead (CREATE)
