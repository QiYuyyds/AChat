## MODIFIED Requirements

### Requirement: Cross-run history serialization SHALL NOT mutate persisted messages

`build_history_for` and all functions it calls (including `prune_old_tool_results`, `fold_old_messages`) MUST NOT modify the `parts`, `status`, or any other column of `Message` rows in the database. Compaction markers produced during history construction are transient — they exist only in the returned `ChatMessage[]` list and MUST NOT be written back to ORM objects that are attached to an active SQLAlchemy session.

When `get_local_db()` (or any session context manager that auto-commits on exit) is used, all `Message` objects loaded within that session MUST be detached (`expunge`) before any compaction logic runs, so that attribute assignments during compaction do not produce dirty objects that would be flushed on commit.

#### Scenario: History construction does not corrupt tool_result parts

- **WHEN** `build_history_for` is called for a conversation with messages containing `tool_result` parts
- **AND** `prune_old_tool_results` replaces some `tool_result` content with compact markers in the returned history
- **THEN** the `Message.parts` column in the database still contains the original `tool_result` content
- **AND** no `Message` row has been marked dirty or flushed during the `build_history_for` call

#### Scenario: Page refresh shows original tool results after multi-turn conversation

- **WHEN** a user has a conversation with 4+ tool-calling turns
- **AND** the user sends a new message (triggering `build_history_for` for the next run)
- **AND** the user refreshes the page (triggering `list_messages` API)
- **THEN** the frontend renders the original `tool_result` content for all completed turns
- **AND** no `[compacted stage=...]` markers appear in the rendered messages

#### Scenario: Detached ORM objects after expunge

- **WHEN** `_build_history_legacy` loads `Message` objects from the database via `get_local_db()`
- **THEN** all loaded `Message` objects are detached from the session via `expunge_all()` before `prune_old_tool_results` is called
- **AND** subsequent `msg.parts_list = ...` assignments do not mark the objects as dirty
- **AND** `session.commit()` on context exit does not flush any `Message` changes
