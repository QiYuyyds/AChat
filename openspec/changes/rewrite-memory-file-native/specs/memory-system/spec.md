# Memory System (Modified)

## REMOVED Requirements

### Requirement: ShortTerm SHALL maintain a sliding window of recent turns
**Reason**: ShortTerm.get_stm_context() has zero callers in the codebase. The in-memory deque is written to but never read. session/ jsonl files replace the "record recent conversations" role.
**Migration**: Remove ShortTerm class, remove stm from MemoryService, remove on_message_end() stm.add() call.

### Requirement: LongTerm SHALL store and recall memories via embedding cosine similarity
**Reason**: Replaced by file-native digest/ with SQLite FTS5 BM25 + wikilink search. Embedding-based retrieval is optional (default off).
**Migration**: Remove LongTerm class, remove ltm from MemoryService, remove LongTermMemory DB table.

### Requirement: LongTerm SHALL perform three-phase consolidation
**Reason**: Replaced by auto_dream pipeline (dream_extract → dream_integrate → dream_topics) which provides typed refinement into two buckets (procedure/wiki) instead of flat dedup/decay/TTL.
**Migration**: Remove consolidation.py.

## MODIFIED Requirements

### Requirement: MemoryService SHALL orchestrate file-native memory pipeline

MemoryService SHALL orchestrate the memory pipeline: auto_memory (conversation → daily cards), auto_index (index maintenance), auto_dream (daily → digest refinement), and proactive (topic surfacing). It SHALL retain ownership of the PG-backed `Preference` store (structured key-value user preferences with three-layer deduplication), which operates alongside the file-native pipeline. Preference extraction in `on_message_end` SHALL continue to run in parallel with auto_memory — Preference handles structured attributes (name, hobbies, location), auto_memory handles narrative facts (decisions, procedures, experiences).

#### Scenario: MemoryService on_message_end triggers auto_memory
- **WHEN** a conversation turn ends
- **THEN** MemoryService triggers auto_memory (background task)
- **AND** session/ jsonl is appended (dual-write with PG Message)

#### Scenario: MemoryService recall uses file search
- **WHEN** PromptAssembler RecallSource requests memory recall
- **THEN** MemoryService delegates to hybrid_search (BM25 + wikilink + RRF)
- **AND** returns top-K results from digest/ and daily/ files

#### Scenario: MemoryService preference remains PG-backed
- **WHEN** PromptAssembler ProfileSource requests user preferences
- **THEN** MemoryService delegates to the existing Preference store (PG `UserPreference` table)
- **AND** `get_preference_context()` returns the formatted preference block unchanged
- **AND** `manage_profile` tool reads/writes preferences via the same PG path
