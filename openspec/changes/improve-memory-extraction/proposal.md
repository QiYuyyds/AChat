# Improve Memory Extraction

## Why

The memory extraction pipeline borrows ideas from mem0 but has structural issues that cause poor extraction quality: (1) LTM extraction only processes assistant replies, missing user-stated facts like "we use React 19"; (2) the k-v extraction format produces unstable keys and forces a fragile classification-routing step that drops or misroutes memories; (3) a prompt bug silently discards the existing-keys dedup instruction. These issues make extracted memories rarely useful and rarely recalled.

## What Changes

- **Replace LTM extraction prompt** with an adapted version of mem0 V3 `ADDITIVE_EXTRACTION_PROMPT` (trimmed ~180 lines). The new prompt feeds the **full conversation** (user + assistant) and outputs **natural language memory strings** instead of k-v pairs.
- **Replace Preference extraction prompt** with an adapted version of mem0 V2 `USER_MEMORY_EXTRACTION_PROMPT`, keeping KV output format for the Preference store.
- **Remove the classification-routing step** (the if/elif/continue block in `extract_memory_from_reply` that routes identity/preference → Preference, fact → LTM, general → drop). All extracted LTM memories go directly to LTM via `store_classified` without category-based routing. **Note**: `classify_memory_content` and `_IMPORTANCE_BY_CATEGORY` are kept — they're still used by `ProfileSource` in `prompt_assembler.py` for preference scoring/sorting. Only `llm_classify_memory` and `_CLASSIFY_SYSTEM_PROMPT` (the LLM-fallback classifier, used exclusively by the routing) are deleted.
- **Fix the `existing_keys` dead-code bug** in `extract_memory_from_reply` (line 216: uses `_EXTRACTION_SYSTEM_PROMPT` constant instead of the modified `system_prompt` variable).
- **Change LTM extraction trigger** from per-message (assistant-only) to per-run (full conversation input). Both user and assistant text are fed to a single LTM extraction call.
- **Simplify V3's dedup/linking**: remove `Recently Extracted Memories`, `Existing Memories` input, and `linked_memory_ids` for the first version. Rely on V3's built-in within-response dedup instruction + existing `store_classified` cosine dedup (≥ 0.95).
- **Conflict resolution**: no active sync between Preference and LTM. ProfileSource (Preference) is the authoritative layer for user profile; RecallSource (LTM) is the supplementary layer. The prompt structure (`【用户画像】` vs `【相关回忆】`) makes authority clear.

## Capabilities

### New Capabilities

- `memory-extraction`: Memory extraction pipeline — how memories are extracted from conversations and routed to Preference (KV) and LTM (natural language) stores. Covers extraction prompts, trigger timing, dedup strategy, and conflict resolution between the two stores.

### Modified Capabilities

_(none — no existing OpenSpec spec defines memory extraction requirements)_

## Impact

- **`backend/app/memory/memory_writer.py`**: Replace `_EXTRACTION_SYSTEM_PROMPT`, rewrite `extract_memory_from_reply` to use V3 prompt + full conversation input + natural language output. Remove `classify_memory_content`, `llm_classify_memory`, `_CLASSIFY_SYSTEM_PROMPT`, `_IMPORTANCE_BY_CATEGORY`. Replace `_PREFERENCE_EXTRACTION_PROMPT` with V2-adapted version. Fix existing_keys bug.
- **`backend/app/memory/memory_service.py`**: Change `on_message_end` flow — LTM extraction receives full conversation (user + assistant) instead of only assistant text. Preference extraction remains per-user-message but uses new prompt.
- **`backend/app/services/agent_runner.py`**: Adjust `_post_run_memory_hook` to pass both user prompt and agent text to a single LTM extraction call.
- **`backend/app/memory/long_term.py`**: `store_classified` still works (receives natural language `content` string), but the `category`/`slot_hint` parameters become optional defaults since classification is removed.
- **`backend/app/services/prompt_assembler.py`**: `ProfileSource` and `RecallSource` unchanged in interface; behavior improves because LTM now has richer natural language memories.
- **No DB schema changes**: `LongTermMemory` table structure unchanged. `category`/`tags`/`slot_hint` columns remain (default to empty/general), can be populated later if needed.
- **No new dependencies**: All changes are prompt text + Python logic.
