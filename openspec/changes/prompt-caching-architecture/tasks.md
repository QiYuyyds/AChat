## 1. PromptAssembler Core: Slot static field + Schema classification

- [x] 1.1 Add `static: bool = False` field to `Slot` dataclass in `prompt_assembler.py`
- [x] 1.2 Mark `SlotConstraints` and `SlotProfile` as `static=True` in all 4 Schemas (CHAT, TOOL, REACT, RAG)
- [x] 1.3 Mark `SlotPlanner`, `SlotTaskMem`, `SlotToolState` as `static=False` (explicit) in REACT_SCHEMA and TOOL_SCHEMA
- [x] 1.4 Remove `SlotRecall` from all 4 Schemas (CHAT, TOOL, REACT, RAG)

## 2. PromptAssembler Core: render_static() / render_dynamic()

- [x] 2.1 Add `render_static(self) -> str` method to `RuntimeContext` — renders only `static=True` slots
- [x] 2.2 Add `render_dynamic(self) -> str` method to `RuntimeContext` — renders only `static=False` slots, wrapped in `<system-reminder>` tags
- [x] 2.3 Add `_get_slot_def(self, kind) -> Slot` helper to `RuntimeContext` for looking up Slot definition from schema

## 3. Agent Runner: Dynamic content injection migration

- [x] 3.1 In `agent_runner.py` L1488-1491: replace `render_system_prompt()` with `render_static()` for system prompt injection
- [x] 3.2 In `agent_runner.py`: add dynamic content injection — `prompt = f"{ctx.render_dynamic()}\n\n{prompt}"` when dynamic content is non-empty
- [x] 3.3 Verify `effective_prompt` variable correctly carries the dynamic prefix through to `AdapterInput.prompt`

## 4. Conversation Context: render_history() sync

- [x] 4.1 In `conversation_context.py` `_build_history_with_assembler()`: change `ctx.render_history()` to use `render_static()` for system messages and `render_dynamic()` as user message prefix
- [x] 4.2 Verify the combined output (system_messages + legacy_messages) still works with the new split

## 5. Compaction: Cache-safe _summarise()

- [x] 5.1 Add `parent_system_prompt: str` parameter to `_summarise()` signature in `context_compaction_service.py`
- [x] 5.2 Construct `messages = [{system: parent_system_prompt}, {user: compaction_prompt + transcript}]` instead of single user message
- [x] 5.3 Remove `tools` parameter from the LLM call (do not pass `tools=parent_tools`)
- [x] 5.4 Add `_get_agent_system_prompt(agent_id)` helper to fetch the agent's system prompt from DB
- [x] 5.5 Modify `compact_conversation()` to call `_get_agent_system_prompt()` and pass it to `_summarise()`

## 6. Cache Metrics: CacheMetrics aggregator

- [x] 6.1 Create `backend/app/infra/cache_metrics.py` with `CacheMetrics` class (sliding window deque, `record()`, `recent_hit_rate()`, `should_alert()`)
- [x] 6.2 Create global `cache_metrics` instance in the module

## 7. Cache Metrics: Fix cache_creation_tokens + integrate

- [x] 7.1 In `custom_adapter.py` L205-213: add extraction of `cache_creation_input_tokens` (Anthropic format) into `run_usage.cache_creation_tokens`
- [x] 7.2 In `custom_adapter.py`: call `cache_metrics.record(cache_read, cache_creation, input)` after each LLM usage chunk is processed
- [x] 7.3 Add warning log when `cache_metrics.should_alert()` returns True
- [x] 7.4 Import `cache_metrics` in `custom_adapter.py`

## 8. Cache Metrics: API endpoint

- [x] 8.1 Add `GET /cache-metrics` endpoint in `backend/app/api/settings.py` returning `{hit_rate, recent_requests, alert}`
- [x] 8.2 Register the route in the API router

## 9. Tests

- [x] 9.1 Test `Slot.static` field and `render_static()` / `render_dynamic()` on `RuntimeContext`
- [x] 9.2 Test that all 4 Schemas have correct static classification (Constraints/Profile=True, others=False)
- [x] 9.3 Test that no Schema contains `SlotRecall` after removal
- [x] 9.4 Test `agent_runner` injects static to system prompt and dynamic to user message
- [x] 9.5 Test `_summarise()` receives and uses `parent_system_prompt` and does not pass tools
- [x] 9.6 Test `CacheMetrics.record()`, `recent_hit_rate()`, and `should_alert()` with mock data
- [x] 9.7 Test `/cache-metrics` API endpoint returns correct shape
- [x] 9.8 Test `cache_creation_tokens` is populated from `cache_creation_input_tokens` field
