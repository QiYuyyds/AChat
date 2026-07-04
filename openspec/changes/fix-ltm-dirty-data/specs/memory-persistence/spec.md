## MODIFIED Requirements

### Requirement: LTM in-memory id SHALL equal the PostgreSQL primary key

`LongTerm` SHALL use each item's PostgreSQL primary key as its in-memory `id`. `load_from_storage` SHALL assign `item.id = row.id` (not an enumeration index), and `_next_id` SHALL be derived from the maximum loaded primary key. This guarantees consolidation's `DELETE`/`UPDATE` statements target the correct rows.

#### Scenario: Loaded items keep their PG primary keys
- **WHEN** `load_from_storage` reads rows with PG ids `[7, 12, 30]`
- **THEN** the in-memory items have ids `7`, `12`, `30` respectively
- **AND** `_next_id` is `31`

#### Scenario: Consolidation delete targets the correct row
- **WHEN** consolidation marks the item with `id=12` for deletion
- **THEN** `_sync_consolidation_to_db` issues `DELETE FROM long_term_memory WHERE id IN (12)`
- **AND** the row previously loaded as PG id 12 is the row removed

#### Scenario: Newly added item keeps its PG id after load coexistence
- **WHEN** a new item is added after `load_from_storage` and PG assigns it `id=31`
- **THEN** the in-memory item's id is `31`
- **AND** it does not collide with any loaded item's id

### Requirement: LTM write paths SHALL be concurrency-safe

`add` and `store_classified` SHALL guard all mutations of shared state (`self.items`, `self._next_id`, `self._items_since_last`) with `self._lock`, consistent with `recall` and `consolidate`. Blocking I/O (embedding) MAY run outside the lock; only in-memory structure mutations are protected.

#### Scenario: Concurrent adds produce unique ids
- **WHEN** two `add` calls run concurrently
- **THEN** the two resulting items have distinct ids
- **AND** both appear exactly once in `self.items`

#### Scenario: Add does not corrupt a concurrent consolidate
- **WHEN** `add` and `consolidate` run concurrently
- **THEN** `self.items` is never observed in a partially-mutated state
- **AND** no item is lost or duplicated

### Requirement: Embedding and generation calls SHALL NOT block the event loop

All embedding and LLM-generation calls made from async memory code paths SHALL be dispatched off the event loop (e.g. `await asyncio.to_thread(...)` or an async client). A slow embedding/generation call SHALL NOT stall unrelated concurrent conversations.

#### Scenario: Slow embedding does not stall other conversations
- **WHEN** an embedding call takes several seconds during LTM recall for conversation A
- **THEN** message handling for concurrent conversation B continues to make progress
- **AND** the event loop is not blocked for the duration of the embedding call
