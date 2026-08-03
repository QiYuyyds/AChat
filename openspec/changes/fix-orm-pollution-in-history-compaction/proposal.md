# Fix ORM Pollution in History Compaction

## Why

`prune_old_tool_results` in `conversation_context.py` writes compacted markers back to ORM `Message` objects while still inside the `get_local_db()` session. The session auto-commits on context exit, permanently overwriting original `tool_result` content in the database with compression markers. After a page refresh, users see `[compacted stage=1 tool=load_skill] [summary: 未知工具结果...]` instead of real tool outputs.

## What Changes

- **Fix ORM object pollution**: `prune_old_tool_results` will operate on detached ORM objects (or plain data containers) instead of session-attached `Message` instances, preventing accidental DB writes during read-only history construction.
- **Expand tool summarizer coverage**: Add dedicated summarizers for `load_skill`, `write_artifact`, `read_artifact`, `update_artifact`, `fs_write`, `fs_edit`, `fs_glob`, `web_search`, and `read_attachment` so they produce meaningful summaries instead of the generic "未知工具结果，保留前 1000 字符" fallback.
- **Add regression test**: Verify that `build_history_for` does not modify persisted `Message.parts` in the database.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `conversation-context`: Add a requirement that cross-run history serialization MUST NOT mutate persisted `Message` records. `prune_old_tool_results` must operate on detached copies.
- `run-internal-compaction`: Add a requirement that the tool summarizer table SHOULD cover all baseline and common optional tools, not just the initial 5.

## Impact

- **Code**: `backend/app/services/conversation_context.py` (`prune_old_tool_results`, `_build_history_legacy`), `backend/app/services/compact_pipeline.py` (`_SUMMARIZERS` table, new summarizer functions)
- **Specs**: `specs/13-conversation-context.md`, `specs/19-unified-agent-loop.md` (if it references compaction tool coverage)
- **Tests**: New regression test in `backend/tests/` verifying DB immutability after `build_history_for`
- **No breaking changes**: All changes are internal — no API contract, event protocol, or DB schema changes
