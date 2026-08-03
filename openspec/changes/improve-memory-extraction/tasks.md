## 1. Replace extraction prompts

- [x] 1.1 Write trimmed V3 `ADDITIVE_EXTRACTION_PROMPT` as `_LTM_EXTRACTION_SYSTEM_PROMPT` in `memory_writer.py`. Trim: remove `Recently Extracted Memories` section, `Existing Memories` section, `Memory Linking` section, `Last k Messages` section, `linked_memory_ids` field, and reduce examples from 12 to 4 (keep: Multi-Topic, Nothing to Extract, Document Extraction, Exhaustive Checklist). Add `use_input_language` Chinese-matching instruction. Target ~180 lines.
- [x] 1.2 Replace `_EXTRACTION_SYSTEM_PROMPT` with the new `_LTM_EXTRACTION_SYSTEM_PROMPT` (keep old constant name as alias for one commit cycle if needed, then remove).
- [x] 1.3 Replace `_PREFERENCE_EXTRACTION_PROMPT` with V2-adapted version. Base it on mem0 `USER_MEMORY_EXTRACTION_PROMPT` (7 info types, few-shot examples, user-only scope). Adapt output instruction to produce KV JSON `{"key": "value"}` instead of `{"facts": ["..."]}`. Keep the existing `_normalize_key` synonym mapping and `_truncate_value` logic.
- [x] 1.4 Verify `_PREFERENCE_MERGE_PROMPT` and `_extract_rule_based` remain unchanged (they serve different purposes: merge consolidation and LLM-unavailable fallback).

## 2. Rewrite LTM extraction function

- [x] 2.1 Replace `extract_memory_from_reply` with `extract_ltm_memories` (new function). New signature: `extract_ltm_memories(generate_fn, embed_fn, ltm, user_msg, assistant_msg, *, agent_id, user_id, existing_keys=None)`. Build the conversation input as `[{"role": "user", "content": user_msg}, {"role": "assistant", "content": assistant_msg}]` and format as V3-style `## New Messages` section.
- [x] 2.2 Parse V3 JSON output `{"memory": [{"id": "0", "text": "...", "attributed_to": "user"}]}`. Strip code fences, handle empty/invalid JSON gracefully (return early).
- [x] 2.3 For each extracted memory: compute embedding via `embed_fn` (off event loop), call `ltm.store_classified` with `category=""`, `tags=[attributed_to_value]`, `slot_hint=""`, `importance=0.5` (default for all; can be refined later). No classification routing, no preference double-write, no category filtering.
- [x] 2.4 Fix the `existing_keys` bug: pass the modified `system_prompt` variable (with existing keys appended) to `generate_fn`, not the original constant `_LTM_EXTRACTION_SYSTEM_PROMPT`.

## 3. Update extraction trigger flow

- [x] 3.1 In `memory_service.py` `on_message_end`: when `role == "assistant"` and `conversation_id` is set, defer LTM extraction to a new method `_safe_extract_ltm(user_msg, assistant_msg, agent_id, user_id)`. The user message must be cached from the preceding `role == "user"` call. Simplest approach: store the last user message on `self._last_user_msg` (a dict keyed by conversation_id) and retrieve it when the assistant message arrives.
- [x] 3.2 In `_safe_extract_ltm`: call `extract_ltm_memories` from `memory_writer` with the cached user message + assistant text.
- [x] 3.3 Keep `_safe_llm_extract_preference` unchanged (still runs on user messages, uses the new V2 prompt). Keep `_safe_extract_session_memory` unchanged.
- [x] 3.4 Update `_post_run_memory_hook` in `agent_runner.py`: the hook already calls `on_message_end` for both user and assistant. No change needed if `on_message_end` handles the cross-referencing internally (step 3.1). Verify the flow works end-to-end.

## 4. Remove classification routing dead code

- [x] 4.1 Delete `llm_classify_memory` function from `memory_writer.py` (only used by the removed routing logic).
- [x] 4.2 Delete `_CLASSIFY_SYSTEM_PROMPT` constant from `memory_writer.py` (only used by `llm_classify_memory`).
- [x] 4.3 Verify `classify_memory_content` and `_IMPORTANCE_BY_CATEGORY` are NOT deleted — they remain in `memory_writer.py` and are still imported by `prompt_assembler.py` `ProfileSource.fetch()` for preference scoring.
- [x] 4.4 Remove the routing if/elif/continue block from the old `extract_memory_from_reply` (this is already replaced by the new `extract_ltm_memories` in task 2.1, but verify no references to the old function remain).

## 5. Verify and test

- [x] 5.1 Run `ruff check backend/app/memory/memory_writer.py backend/app/memory/memory_service.py` — fix any lint errors.
- [x] 5.2 Run `ruff check backend/app/services/agent_runner.py` — fix any lint errors from the hook changes.
- [x] 5.3 Verify `prompt_assembler.py` imports of `classify_memory_content` and `_IMPORTANCE_BY_CATEGORY` still resolve (they're kept, not deleted).
- [x] 5.4 Write a simple integration test: mock `generate_fn` to return a V3-format JSON, call `extract_ltm_memories`, verify memories land in LTM with embeddings and tags.
- [x] 5.5 Write a test for the trivial-reply path: mock `generate_fn` returning `{"memory": []}`, verify no LTM items created.
- [x] 5.6 Run `pytest backend/tests/` — fix any regressions.
