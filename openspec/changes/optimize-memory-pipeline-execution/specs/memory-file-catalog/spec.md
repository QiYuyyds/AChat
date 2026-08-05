# Memory File Catalog

## ADDED Requirements

### Requirement: File catalog SHALL track memory file paths and modification times

A SQLite table `memory_catalog(path TEXT PK, st_mtime REAL, bucket TEXT)` SHALL track every memory file under `daily/` and `digest/`. The catalog SHALL be stored in `<agenthub-data>/memory/metadata/catalog.db`. On service startup, a full reindex SHALL reconcile the catalog with the filesystem: new files are added, deleted files are removed, and mtime changes are detected.

#### Scenario: Service startup reconciles catalog
- **WHEN** the memory service starts
- **THEN** a full scan of `daily/` and `digest/` directories is performed
- **AND** files not in the catalog are added with their current st_mtime
- **AND** files in the catalog but missing from disk are removed
- **AND** files with changed st_mtime are marked as changed

#### Scenario: File write updates catalog
- **WHEN** auto_memory or auto_dream writes a new or updated file
- **THEN** the catalog entry for that path is upserted with the current st_mtime

### Requirement: dream_extract SHALL use catalog to detect changed files

dream_extract SHALL query the file catalog to identify files whose filesystem st_mtime differs from the catalog's recorded st_mtime. Only changed files SHALL be processed by the extract step. Deleted files (catalog has entry, filesystem does not) SHALL be skipped and their catalog entry removed.

#### Scenario: Only changed files are processed
- **WHEN** dream_extract runs and 3 out of 10 daily files have changed since last run
- **THEN** only the 3 changed files are sent to the LLM for extraction
- **AND** the 7 unchanged files are skipped

#### Scenario: Deleted file is cleaned from catalog
- **WHEN** dream_extract detects a catalog entry whose file no longer exists on disk
- **THEN** the catalog entry is removed
- **AND** no extraction is attempted for the deleted file

### Requirement: File catalog SHALL support bucket filtering

The catalog SHALL store a `bucket` column (`daily`, `procedure`, `wiki`) for each file. Consumers SHALL be able to query files by bucket for targeted processing.

#### Scenario: dream_extract queries daily bucket only
- **WHEN** dream_extract queries changed files
- **THEN** only files with `bucket = 'daily'` are returned
- **AND** digest files are excluded from the changed set
