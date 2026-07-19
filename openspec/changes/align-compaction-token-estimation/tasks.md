## 1. 新增 estimate_dict_message_tokens 共享函数

- [x] 1.1 在 `backend/app/services/transcript_renderer.py` 中新增 `estimate_dict_message_tokens(msg: dict, include_reasoning: bool = False) -> int`：算 `content`（str 或 list[dict] 时取 `type=text` 的 `text` 字段）+ `tool_calls[*].function.name` + `tool_calls[*].function.arguments` + `reasoning_content`（仅 `include_reasoning=True` 时）+ 4 tokens overhead。不算 `role` / `tool_call_id` / `type` 等元数据。逻辑与 `compact_pipeline.estimate_messages_tokens` 的单条 message 部分一致（含 reasoning_content 时）。
- [x] 1.2 在 `transcript_renderer.py` 模块顶部 docstring 中添加三个估算函数的关系文档化注释（见 design D3）。
- [x] 1.3 编写测试 `test_estimate_dict_message_tokens_basic`：构造含 content + tool_calls 的 dict，断言估算 = `estimate_tokens(content) + estimate_tokens(name + arguments) + 4`。
- [x] 1.4 编写测试 `test_estimate_dict_message_tokens_excludes_metadata`：断言 `role` / `tool_call_id` / `type` 不算 token。
- [x] 1.5 编写测试 `test_estimate_dict_message_tokens_include_reasoning`：`include_reasoning=True` 时算 reasoning_content，`False` 时不算。
- [x] 1.6 编写测试 `test_estimate_dict_message_tokens_list_content`：当 `content` 是 list 时（vision parts），只算 `type=text` 的 `text` 字段，跳过 `image_url`。

## 2. Tier 0 estimate_messages_tokens 内部重构

- [x] 2.1 修改 `backend/app/services/compact_pipeline.py` 的 `estimate_messages_tokens`：函数体改为 `from app.services.transcript_renderer import estimate_dict_message_tokens` + `return sum(estimate_dict_message_tokens(m, include_reasoning=True) for m in messages)`。用延迟导入打破循环依赖。
- [x] 2.2 编写回归测试 `test_estimate_messages_tokens_unchanged_after_refactor`：用改造前的 `estimate_messages_tokens` 逻辑构造期望值，断言改造后返回值一致。
- [x] 2.3 运行现有 `backend/tests/test_compact_pipeline.py::test_estimate_messages_tokens_*` 全过。

## 3. Tier 4 _estimate_chat_message_tokens 替换

- [x] 3.1 在 `backend/app/services/conversation_context.py` 中删除 `_estimate_chat_message_tokens` 函数（line 441-458）。
- [x] 3.2 在文件顶部导入 `from app.services.transcript_renderer import estimate_dict_message_tokens`。
- [x] 3.3 将所有调用 `_estimate_chat_message_tokens(m)` 的地方改为 `estimate_dict_message_tokens(m, include_reasoning=False)`。
- [x] 3.4 编写回归测试 `test_tier4_token_estimate_unchanged_after_refactor`：构造一个含 content + tool_calls 的 chat message dict，断言 `estimate_dict_message_tokens(m, include_reasoning=False)` 返回值与原 `_estimate_chat_message_tokens(m)` 一致。
- [x] 3.5 运行现有 `backend/tests/test_conversation_context*.py` 全过。

## 4. 回归与自检

- [x] 4.1 运行 `ruff check backend/app/services/transcript_renderer.py backend/app/services/compact_pipeline.py backend/app/services/conversation_context.py` 全过。
- [x] 4.2 运行 `pytest backend/tests/test_compact_pipeline.py backend/tests/test_compact_markers.py` 全过（Tier 0 回归）。
- [x] 4.3 运行 `pytest backend/tests/test_conversation_context*.py` 全过（Tier 4 回归）。
- [x] 4.4 运行 `pytest backend/tests/test_transcript_renderer.py` 全过（含新增测试）。
- [x] 4.5 确认无循环依赖：`python -c "from app.services.compact_pipeline import estimate_messages_tokens; from app.services.transcript_renderer import estimate_dict_message_tokens"` 不报错。
- [x] 4.6 确认无遗留 `print()` / `TODO` / 注释代码块。
- [x] 4.7 确认 `estimate_messages_tokens` 的外部接口和返回值不变。
- [x] 4.8 确认无新增第三方依赖。
