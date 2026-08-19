## ADDED Requirements

### Requirement: Long-term memory search UI does not crash on search mode
The long-term memory panel SHALL import every lucide-react icon it renders. When the user enters search mode (non-empty query submitted), the panel MUST render the clear-search control without a runtime ReferenceError.

#### Scenario: Enter search mode shows clear control
- **WHEN** the user submits a non-empty search query in the long-term memory panel
- **THEN** the panel enters search mode and shows a clear-search control without throwing `ReferenceError: X is not defined`

#### Scenario: Edit mode back control
- **WHEN** the user opens a memory file and enters edit mode
- **THEN** the back/return control renders without a runtime ReferenceError for missing icon imports

### Requirement: Search API returns workspace-relative paths
`GET /api/memory/search` SHALL return each hit's `path` as a path relative to the memory workspace root, consistent with `GET /api/memory/files` list items, so that clients can open the file via `GET /api/memory/files/{path}`.

#### Scenario: Search hit path is relative
- **WHEN** a memory file is indexed and a query matches it
- **THEN** the search response item's `path` does not include the absolute workspace root prefix
- **AND** the path is usable as the `{path}` segment of `GET /api/memory/files/{path}`

#### Scenario: Search hit can be opened as detail
- **WHEN** the client receives a search hit with `path`
- **AND** the client requests `GET /api/memory/files/{path}` (path URL-encoded as today)
- **THEN** the API returns 200 with the file body when the file still exists

### Requirement: Index stores relative paths for memory files
The memory auto-index SHALL store BM25 and wikilink document keys as workspace-relative paths for daily and digest files, so search results and subsequent reindex cycles remain portable across process restarts and DATA_DIR locations.

#### Scenario: Incremental index after write
- **WHEN** a memory file is written and `auto_index.index_file` runs
- **THEN** the BM25 entry path for that file is relative to the memory workspace root

#### Scenario: Full reindex rebuilds relative paths
- **WHEN** `full_reindex` runs after the relative-path change is deployed
- **THEN** all indexed memory file paths are relative to the memory workspace root
