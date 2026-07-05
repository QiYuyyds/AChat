## 1. Profile slot: static→dynamic + remove top_k

- [x] 1.1 In `prompt_assembler.py` CHAT_SCHEMA: change `Slot(kind=SlotProfile, static=True, ...)` to `static=False`, remove `top_k` from SlotFilter
- [x] 1.2 In `prompt_assembler.py` TOOL_SCHEMA: same change — `static=False`, remove `top_k`
- [x] 1.3 In `prompt_assembler.py` REACT_SCHEMA: same change — `static=False`, remove `top_k`
- [x] 1.4 In `prompt_assembler.py` RAG_SCHEMA: same change — `static=False`, remove `top_k`

## 2. ProfileSource: drop LTM + add score

- [x] 2.1 In `prompt_assembler.py` ProfileSource: remove `ltm` parameter from `__init__`, remove the LTM `filter_by_category` block from `fetch()`
- [x] 2.2 In `prompt_assembler.py` ProfileSource.fetch(): import `classify_memory_content` and `_IMPORTANCE_BY_CATEGORY` from `memory_writer`, compute `score` per item
- [x] 2.3 In `prompt_assembler.py` ProfileSource.fetch(): sort items by `score` descending before returning (instead of `sorted(prefs.keys())`)
- [x] 2.4 In `prompt_assembler.py` ProfileSource.supports(): remove `SlotRecall` from the supported kinds (only support `SlotProfile`)
- [x] 2.5 In `main.py`: change `ProfileSource(preference_provider=..., ltm=...)` to `ProfileSource(preference_provider=...)` — drop `ltm` argument

## 3. Preference key normalization (Layer 1 dedup)

- [x] 3.1 In `preference.py`: add `_KEY_SYNONYMS` dict mapping common synonyms to canonical keys (喜欢→喜好, 偏好→喜好, 偏爱→喜好, 爱好→喜好, 名字→姓名, 名称→姓名, 编程语言→语言, 编程偏好→语言)
- [x] 3.2 In `preference.py`: add `_normalize_key(key: str) -> str` function that returns `_KEY_SYNONYMS.get(key, key)`
- [x] 3.3 In `preference.py` `set()`: call `_normalize_key(key)` before writing to in-memory dict and PG
- [x] 3.4 In `preference.py` `save_batch()`: call `_normalize_key(k)` before passing to `set()`
- [x] 3.5 In `preference.py` `extract_and_save_sync()`: call `_normalize_key(key)` before writing to in-memory dict

## 4. LLM extraction: pass existing keys (Layer 2 dedup)

- [x] 4.1 In `memory_writer.py` `extract_preferences()`: add `existing_keys: Optional[List[str]] = None` parameter
- [x] 4.2 In `memory_writer.py` `_PREFERENCE_EXTRACTION_PROMPT`: when `existing_keys` is provided, append rule "4. 已有的偏好 key：{existing_keys}。如果新提取的偏好与已有 key 语义相同，必须复用已有 key"
- [x] 4.3 In `memory_service.py` `_safe_llm_extract_preference()`: pass `existing_keys=list(self.preference.data.keys())` to `extract_preferences()`
- [x] 4.4 In `memory_service.py` `_safe_extract_memory()` → `extract_memory_from_reply()`: pass `existing_keys` via the `preference` object's `data.keys()` (already available since preference store is passed)

## 5. Preference periodic LLM consolidation (Layer 3 dedup)

- [x] 5.1 In `memory_service.py`: add `_consolidate_preferences()` method that sends all `preference.data` to LLM with a merge prompt, receives merged dict, and calls `save_batch()` + deletes removed keys
- [x] 5.2 In `memory_service.py` `_safe_consolidate()`: after LTM consolidation, check `if len(self.preference.data) > 15` and call `await self._consolidate_preferences()`
- [x] 5.3 In `memory_writer.py`: add `_PREFERENCE_MERGE_PROMPT` constant for the LLM consolidation prompt

## 6. Tool failure memory extraction (already implemented)

- [x] 6.1 In `agent_runner.py` `_post_run_memory_hook`: extract `tool_use` parts to build `callId→toolName` mapping
- [x] 6.2 In `agent_runner.py` `_post_run_memory_hook`: extract `tool_result` parts with `isError=True`, format as error text
- [x] 6.3 In `agent_runner.py` `_post_run_memory_hook`: append error block to `agent_text` before calling `on_message_end("assistant", ...)`

## 7. Tests

- [x] 7.1 Test ProfileSlot is `static=False` in all 4 Schemas (CHAT, TOOL, REACT, RAG)
- [x] 7.2 Test ProfileSource.fetch() returns items only from preference_provider (no LTM call)
- [x] 7.3 Test ProfileSource.fetch() assigns correct scores (identity=0.9, preference=0.7, unclassified=0.3)
- [x] 7.4 Test ProfileSource.fetch() sorts items by score descending
- [x] 7.5 Test `_normalize_key("喜欢")` returns "喜好"; `_normalize_key("unknown")` returns "unknown"
- [x] 7.6 Test `preference.set("喜欢", "Python")` normalizes to "喜好" and updates existing entry
- [x] 7.7 Test `extract_preferences()` with `existing_keys=["喜好"]` produces prompt containing the key list
- [x] 7.8 Test `_consolidate_preferences()` merges duplicate keys when count > threshold
- [x] 7.9 Test `_post_run_memory_hook` appends tool error block when `isError=True` parts exist
- [x] 7.10 Test `_post_run_memory_hook` does not append error block when no tool errors
