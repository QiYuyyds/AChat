## ADDED Requirements

### Requirement: Importance decay SHALL be incremental and persisted

Consolidation SHALL decay each item's importance based on the time elapsed since that item's last decay checkpoint (not the total age since creation), and SHALL persist the decayed importance back to PostgreSQL. Repeated consolidation runs SHALL NOT compound-decay an already-decayed item.

#### Scenario: Second consolidation does not re-decay from creation
- **WHEN** `consolidate()` runs, decays an item, then `consolidate()` runs again shortly after
- **THEN** the second run decays the item only by the small interval elapsed since the first run
- **AND** the item's importance is not reduced as if decayed from `created_at` a second time

#### Scenario: Decayed importance is written to PG
- **WHEN** consolidation decays an item's importance from 0.50 to 0.48
- **THEN** the corresponding `long_term_memory` row's `importance` column is updated to 0.48

#### Scenario: Restart does not retroactively re-decay
- **WHEN** the process restarts and `load_from_storage` runs
- **THEN** each loaded item's decay checkpoint is set to the load time
- **AND** the already-persisted importance is not decayed again for the pre-restart idle period

### Requirement: Dedup merges SHALL persist the surviving item

When consolidation deduplicates a pair (similarity ≥ `dedup_threshold`), it SHALL delete the absorbed item from PG and SHALL persist the surviving item's updated fields (importance, tags, last_accessed) via `update_in_db`.

#### Scenario: Surviving item is updated in PG after dedup
- **WHEN** items A and B are deduped, A survives with merged tags and `max` importance
- **THEN** B's row is deleted from `long_term_memory`
- **AND** A's row is updated in `long_term_memory` with the merged tags and importance

#### Scenario: Dedup result survives restart
- **WHEN** a dedup merge completes and the process later restarts
- **THEN** `load_from_storage` reflects the merged state (B absent, A updated)
- **AND** the merge is not silently reverted

### Requirement: Consolidation deletions SHALL target rows by PG primary key

Consolidation's `delete_from_db` and `update_in_db` SHALL reference items by their PostgreSQL primary key (guaranteed by the persistence capability's id requirement), so that no correct row is left behind and no unrelated row is affected.

#### Scenario: Expiry removes exactly the expired rows
- **WHEN** consolidation expires items with ids `[5, 9]`
- **THEN** only rows with `id` 5 and 9 are deleted from `long_term_memory`
- **AND** no other row is deleted
