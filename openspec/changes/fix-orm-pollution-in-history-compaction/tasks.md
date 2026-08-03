## 1. Fix ORM Object Pollution (Core Bug)

- [x] 1.1 In `_build_history_legacy` (`conversation_context.py`), add `db.expunge_all()` after loading `recent` and `pinned` Message objects, before `prune_old_tool_results` is called
- [x] 1.2 Verify `_build_history_with_assembler` path is unaffected (it delegates to `_build_history_legacy` for message serialization, so the expunge covers both paths)
- [x] 1.3 Add a code comment in `prune_old_tool_results` clarifying that callers MUST ensure ORM objects are detached before calling this function
- [x] 1.4 Run `ruff check backend/app/services/conversation_context.py` and fix any lint issues

## 2. Expand Tool Summarizer Coverage

- [x] 2.1 Add `_summarize_load_skill` to `compact_pipeline.py` — extract skill name + first 200 chars of description
- [x] 2.2 Add `_summarize_write_artifact` — extract artifactId + title + type
- [x] 2.3 Add `_summarize_read_artifact` — extract artifactId + title + first 500 chars of content
- [x] 2.4 Add `_summarize_update_artifact` — extract artifactId + version + summary
- [x] 2.5 Add `_summarize_fs_write` — extract path + bytes written
- [x] 2.6 Add `_summarize_fs_edit` — extract path + lines changed summary
- [x] 2.7 Add `_summarize_fs_glob` — extract pattern + first 10 matches
- [x] 2.8 Add `_summarize_web_search` — extract query + first 5 result titles + URLs
- [x] 2.9 Add `_summarize_read_attachment` — extract fileName + first 500 chars
- [x] 2.10 Add `_summarize_deploy` (shared by `deploy_artifact` + `deploy_workspace`) — extract status + preview URL
- [x] 2.11 Add `_summarize_task_dispatch` — extract agentId + status + result head (200 chars)
- [x] 2.12 Add `_summarize_dispatch_plan` — extract task count + statuses summary
- [x] 2.13 Add `_summarize_plan_tools` (shared by `create_plan` / `plan_step` / `add_plan_steps`) — extract planId + step count
- [x] 2.14 Add `_summarize_manage_tools` (shared by all 7 `manage_*` tools) — extract action + result status
- [x] 2.15 Add `_summarize_ask_user` — extract question + answer head (200 chars)
- [x] 2.16 Register all new summarizers in the `_SUMMARIZERS` dispatch table
- [x] 2.17 Add a `logger.warning` in `_summarize_unknown` when an unknown tool is encountered (for discoverability)
- [x] 2.18 Run `ruff check backend/app/services/compact_pipeline.py` and fix any lint issues

## 3. Regression Test

- [x] 3.1 Create `backend/tests/test_history_compaction_orm_safety.py` with a test that seeds a conversation with 5+ tool-calling turns (using various tool types), calls `build_history_for`, then re-reads messages from DB and asserts original `tool_result` content is preserved
- [x] 3.2 Add a test case verifying that `prune_old_tool_results` on detached objects does not produce dirty state
- [x] 3.3 Add a test case verifying that new summarizers produce shorter output than the original for each tool type
- [x] 3.4 Run `pytest backend/tests/test_history_compaction_orm_safety.py -v` and ensure all tests pass

## 4. Spec Documentation Sync

- [x] 4.1 Update `specs/13-conversation-context.md` to note the ORM detachment requirement in the compaction section
- [x] 4.2 Update `specs/19-unified-agent-loop.md` (or the relevant compaction spec) to list the expanded tool summarizer coverage
- [x] 4.3 Verify `openspec status --change "fix-orm-pollution-in-history-compaction"` shows all artifacts as done
