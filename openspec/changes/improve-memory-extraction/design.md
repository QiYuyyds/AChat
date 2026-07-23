# Design: Improve Memory Extraction

## Context

AChat's memory system borrows from mem0's design but has several structural issues in the extraction pipeline. The current system has two stores — **Preference** (KV, injected as `【用户画像】`) and **LTM** (natural language + embedding, recalled via `RecallSource` as `【相关回忆】`). Extraction quality is poor because:

1. **Input scope**: LTM extraction only runs on assistant replies; user-stated facts ("we use React 19") never reach LTM unless the assistant restates them.
2. **Output format**: k-v pairs (`{"key": "value"}`) force unstable keys and a fragile classification-routing step that drops or misroutes memories.
3. **Dead-code bug**: `extract_memory_from_reply` builds a modified `system_prompt` with existing preference keys, then calls `generate_fn` with the original constant `_EXTRACTION_SYSTEM_PROMPT` — the dedup instruction never reaches the LLM.
4. **Classification routing**: `classify_memory_content` (keyword matching) + `llm_classify_memory` (LLM fallback) route each extracted fact to Preference/LTM/drop based on category. "general" category facts are silently dropped.

Key files: `backend/app/memory/memory_writer.py` (extraction logic + prompts), `backend/app/memory/memory_service.py` (trigger flow), `backend/app/services/agent_runner.py` (`_post_run_memory_hook`).

## Goals / Non-Goals

**Goals:**
- LTM extraction receives the **full conversation** (user + assistant) and produces **natural language memory strings** (not k-v).
- Preference extraction uses a mem0 V2-inspired prompt with broader coverage than the current narrow "personal preference" prompt.
- Classification-routing step is **removed** — all LTM-extracted memories go directly to LTM.
- The `existing_keys` dead-code bug is fixed.
- No DB schema changes, no new dependencies.

**Non-Goals:**
- mem0 V3's `linked_memory_ids` graph-linking mechanism (requires GraphMemory infrastructure changes — future work).
- mem0 V3's `Existing Memories` dedup input (passing existing LTM items into the extraction prompt — future optimization to reduce redundant extractions).
- mem0's `DEFAULT_UPDATE_MEMORY_PROMPT` ADD/UPDATE/DELETE decision logic (requires a second LLM call per extraction — future work).
- Fixing the consolidation trigger issue (`_items_since_last` rarely increments) — separate change.
- Adding `recall_memory` slot to PromptAssembler schemas — separate change.
- Recall threshold/top_k tuning — separate change.

## Decisions

### D1: Use mem0 V3 `ADDITIVE_EXTRACTION_PROMPT` (trimmed) for LTM extraction

**Rationale**: V3 extracts from both user and assistant messages, outputs self-contained natural language memory strings, has built-in within-response dedup, and covers 7 information types (preferences, personal details, plans, activities, health, professional, miscellaneous). This directly fixes the input-scope and output-format problems.

**Alternative considered**: V1 `FACT_RETRIEVAL_PROMPT` (simpler, ~60 lines) — rejected because V3's quality standards (contextually rich, self-contained, temporally grounded, numerically precise) produce more recallable memories. V3 is trimmed from ~450 to ~180 lines by removing the linked-memories, existing-memories, and recently-extracted sections, plus reducing examples from 12 to 4.

**Alternative considered**: V2 split prompts (user-only + agent-only) — rejected because running two separate extraction calls doubles LLM cost and the V3 single-pass approach produces better cross-referenced memories.

### D2: Use mem0 V2 `USER_MEMORY_EXTRACTION_PROMPT` (adapted) for Preference extraction

**Rationale**: V2's user-extraction prompt covers the same 7 information types as V3 but is explicitly scoped to user messages only, matching the Preference store's purpose (user profile). Adapting it to KV output (instead of facts list) is straightforward — the prompt already produces concise factual statements.

**Alternative considered**: Keep the current `_PREFERENCE_EXTRACTION_PROMPT` — rejected because it's too narrow ("只提取个人偏好") and misses professional details, plans, and incidental facts.

### D3: Remove classification routing entirely

**Rationale**: The classification step (`classify_memory_content` + `llm_classify_memory`) is the root cause of memory fragmentation. It routes identity/preference → Preference table (excluded from LTM recall), fact/episodic → LTM, and general → drop. This means useful memories are dropped or sent to the wrong store. With V3 extracting all types to LTM as natural language, and V2 extracting identity/preference to Preference as KV, the two stores are separated by **extraction prompt** (not post-hoc routing), which is more reliable.

**Alternative considered**: Keep classification but simplify categories — rejected because keyword matching is inherently fragile and LLM classification adds latency for marginal benefit.

### D4: Conflict resolution via prompt-layer hierarchy (no active sync)

**Rationale**: When both stores contain identity/preference info, `ProfileSource` (Preference, injected as `【用户画像】`) is authoritative and `RecallSource` (LTM, injected as `【相关回忆】`) is supplementary. The prompt structure makes authority clear. Stale LTM items are handled passively by the existing consolidation pipeline (cosine dedup + decay + TTL).

**Edge case**: If a user updates Preference via `manage_profile` tool (not via conversation), LTM may have stale identity info. This is acceptable because ProfileSource is authoritative — the LLM sees `【用户画像】姓名: 李四` before `【相关回忆】用户叫张三`.

**Alternative considered**: Preference-update-triggered LTM cleanup — rejected as too complex for v1; can be added as a hook later.

### D5: Single LTM extraction call per run (not per message)

**Rationale**: The current `_post_run_memory_hook` calls `on_message_end` separately for user and assistant. With V3 requiring full-conversation input, a single extraction call after the run completes is more natural and costs one LLM call instead of two.

**Implementation**: `_post_run_memory_hook` already has both `prompt` (user) and `agent_text` (assistant). Pass both to a new `extract_ltm_memories(generate_fn, embed_fn, ltm, user_msg, assistant_msg, ...)` function. `on_message_end` retains the per-message Preference extraction (user-only) and STM/ChatHistory writes.

### D6: V3's `attributed_to` field stored as a tag

**Rationale**: V3 outputs `{"text": "...", "attributed_to": "user"}`. The `attributed_to` field can be stored in the existing `Item.tags` list (e.g., `["user"]` or `["assistant"]`) without schema changes. This preserves the source attribution for future filtering.

## Risks / Trade-offs

- **[V3 prompt length]** ~180 lines of system prompt per extraction call increases token cost by ~1500 tokens vs the current ~200-token prompt. → Acceptable: extraction runs as a background `asyncio.create_task`, not on the critical path. Can further trim examples if cost is a concern.

- **[Duplicate memories across stores]** LTM may contain "用户叫张三" while Preference has `姓名=张三`. → Accepted: this is redundant, not contradictory. ProfileSource is authoritative. The narrative form in LTM adds context (e.g., "用户改名为李四" tells a story KV can't).

- **[No active conflict resolution]** Stale LTM items can persist after Preference updates outside conversation. → Mitigated by prompt hierarchy (画像 > 回忆). Long-term: consolidation's cosine dedup will eventually merge similar items. Can add a Preference-update → LTM-cleanup hook if needed.

- **[Classification removal breaks `store_classified` signature]** `store_classified` receives `category`/`tags`/`slot_hint` parameters. → These parameters default to empty strings/lists. The function still works; `category` just defaults to `"general"`.

- **[V3 prompt is English, conversations are Chinese]** V3 says "detect the language of the user input and record the facts in the same language." → The prompt itself can be in English (LLM handles cross-lingual instruction); extracted memories will be in Chinese (matching conversation language). No translation needed.

## Migration Plan

1. Replace prompts in `memory_writer.py` (no data migration).
2. Rewrite `extract_memory_from_reply` → `extract_ltm_memories` (new function signature).
3. Update `on_message_end` in `memory_service.py` to call the new function with full conversation.
4. Update `_post_run_memory_hook` in `agent_runner.py` to pass both user + assistant text.
5. Remove dead code: `classify_memory_content`, `llm_classify_memory`, `_CLASSIFY_SYSTEM_PROMPT`, `_IMPORTANCE_BY_CATEGORY`.
6. Existing LTM items (stored with old k-v format) remain — no migration needed. New items will be natural language. Consolidation's cosine dedup handles both formats.

**Rollback**: Revert `memory_writer.py` + `memory_service.py` + `agent_runner.py` changes. No data was migrated.

## Open Questions

- Should the V3 prompt be fully translated to Chinese, or kept in English with a Chinese-language instruction? (Current decision: keep English prompt, add `use_input_language` flag — matches mem0's approach.)
- Should `attributed_to` get its own column in `LongTermMemory` instead of stuffing into `tags`? (Current decision: use tags — avoids schema change. Can migrate later.)
