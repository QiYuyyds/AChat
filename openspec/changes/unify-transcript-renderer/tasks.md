## 1. 新建共享模块 transcript_renderer.py

- [x] 1.1 创建 `backend/app/services/transcript_renderer.py`，定义 `__all__` 导出 `render_tool_aware_transcript` 和 `estimate_full_message_tokens`。
- [x] 1.2 实现 `render_tool_aware_transcript(messages: list[Message], agent_names: dict[str, str] | None = None) -> str`：遍历 messages，按 role 分支渲染。user/system 渲染为 `<role>：<text>`；agent 渲染为多行（text + tool_use + tool_result）。跳过 thinking part。跳过空消息（无 text 且无 tool part）。
- [x] 1.3 在 agent 分支中，对每个 `tool_use` part 渲染 `  ↳ tool_use: <toolName>(<args_json>)`，args 用 `json.dumps(args, ensure_ascii=False)` 序列化（超过 200 字符截断）。
- [x] 1.4 在 agent 分支中，对每个 `tool_result` part：先从同消息的 `tool_use` part 中按 `callId` 匹配找到 `toolName` 和 `args`；调用 `compact_pipeline.summarize_tool_result(tool_name, args, content, stage=1)` 获取压缩内容；渲染为 `  ↳ tool_result: [<tool_name>] <summary> | <compressed_content>`。找不到配对的 tool_use 时用 `unknown` 作为 tool_name。
- [x] 1.5 实现 `estimate_full_message_tokens(messages: list[Message]) -> int`：遍历所有 part，text/thinking/effective_prompt 算 `estimate_tokens(content)`，tool_use 算 `estimate_tokens(json.dumps(args))`，tool_result 算 `estimate_tokens(result_str)`。逻辑与 `context_compaction_service._message_token_estimate` 一致，提取为公共函数。
- [x] 1.6 编写单元测试 `backend/tests/test_transcript_renderer.py::test_render_tool_aware_transcript_includes_tool_use`：构造含 tool_use + tool_result 的 agent message，断言 transcript 包含 `↳ tool_use:` 和 `↳ tool_result:` 行。
- [x] 1.7 编写测试 `test_render_skips_thinking_parts`：构造含 thinking part 的 agent message，断言 transcript 不包含 thinking 内容。
- [x] 1.8 编写测试 `test_render_compresses_fs_list_result`：构造 `fs_list(depth=3)` 的 tool_result（500 entries），断言 transcript 中的 tool_result 行长度 < 原始内容的 30%。
- [x] 1.9 编写测试 `test_render_preserves_code_explore_verbatim`：构造 code_explore 的 tool_result，断言 transcript 中完整保留。
- [x] 1.10 编写测试 `test_estimate_full_message_tokens_includes_tool_parts`：构造含 500-token text + 50k-token tool_result 的消息，断言估算 >> 500。

## 2. Session Memory 接入共享模块

- [x] 2.1 在 `backend/app/memory/session_memory.py` 中删除 `_render_transcript` 和 `_message_text` 函数（line 287-311）。
- [x] 2.2 在文件顶部导入 `from app.services.transcript_renderer import render_tool_aware_transcript, estimate_full_message_tokens`。
- [x] 2.3 将 `extract` 方法中的 `recent_transcript = _render_transcript(messages)` 改为 `recent_transcript = render_tool_aware_transcript(messages)`。
- [x] 2.4 将 `should_extract` 方法中的 `total_tokens = sum(estimate_tokens(_message_text(m)) for m in messages)` 改为 `total_tokens = estimate_full_message_tokens(messages)`。
- [x] 2.5 将 `should_extract` 中 `token_since = total_tokens - estimate_tokens(existing.summary)` 保持不变（summary 本身是纯文本，用 `estimate_tokens` 估算是正确的）。
- [x] 2.6 增强 `extract` 的 system prompt（line 119-124），在现有保留维度后追加："- 已探索的文件/目录结构（路径 + 关键发现）\n- 执行过的关键命令及其结果摘要\n- 架构理解与代码结构发现\n"。
- [x] 2.7 编写测试 `test_session_memory_should_extract_uses_full_token_estimate`：构造含大量 tool_result 的消息列表，断言 `should_extract` 返回 True（而旧 text-only 估算会返回 False）。
- [x] 2.8 编写测试 `test_session_memory_extract_transcript_contains_tool_info`：mock `_generate_fn`，断言传给 generate_fn 的 user_msg 包含 `↳ tool_use:` 和 `↳ tool_result:` 行。

## 3. Tier 2/3 接入共享模块

- [x] 3.1 在 `backend/app/services/context_compaction_service.py` 中删除 `_render_transcript` 和 `_message_text` 函数（line 556-580）。
- [x] 3.2 在文件顶部导入 `from app.services.transcript_renderer import render_tool_aware_transcript, estimate_full_message_tokens`。
- [x] 3.3 将 `compact_conversation` 中的 `full_transcript = _render_transcript(to_compact, agent_names)` 改为 `full_transcript = render_tool_aware_transcript(to_compact, agent_names)`。
- [x] 3.4 将 Case 2（partial coverage）中的 `gap_transcript = _render_transcript(gap_messages, agent_names)` 改为 `render_tool_aware_transcript(gap_messages, agent_names)`。
- [x] 3.5 将 `_message_token_estimate` 函数体替换为调用 `estimate_full_message_tokens([msg])` 返回单条消息的估算（或直接删除并让 `estimate_uncompacted_tokens` 调用 `estimate_full_message_tokens(rows)`）。注意：`estimate_uncompacted_tokens` 已经用 `_message_token_estimate` 逐条累加，改为 `estimate_full_message_tokens(rows)` 一次调用即可。
- [x] 3.6 将 `ctx_before` 计算中的 `sum(estimate_tokens(_message_text(m)) for m in kept)` 改为 `sum(estimate_full_message_tokens([m]) for m in kept)`（或直接 `estimate_full_message_tokens(kept)`）。
- [x] 3.7 将 `ctx_after` 计算中的 `sum(estimate_tokens(_message_text(m)) for m in kept)` 同上修改。
- [x] 3.8 增强 `_summarise` 的 prompt（line 624-633），在现有保留维度后追加同样的结构化维度（文件结构、命令结果、架构发现）。
- [x] 3.9 编写测试 `test_compact_transcript_contains_tool_info`：构造含 tool_use + tool_result 的消息列表，调用 `compact_conversation`（mock LLM），断言传给 LLM 的 prompt 包含 `↳ tool_use:` 和 `↳ tool_result:` 行。
- [x] 3.10 编写测试 `test_estimate_uncompacted_tokens_includes_tool_parts`：构造含 tool_result 的消息列表，断言 `estimate_uncompacted_tokens` 返回值 > text-only 估算。

## 4. 回归与自检

- [x] 4.1 运行 `ruff check backend/app/services/transcript_renderer.py backend/app/memory/session_memory.py backend/app/services/context_compaction_service.py` 全过。
- [x] 4.2 运行 `pytest backend/tests/test_transcript_renderer.py` 全过。
- [x] 4.3 运行现有 `backend/tests/test_context_compact*.py` 全过（回归：compact_conversation 外部行为不变）。
- [x] 4.4 运行现有 Session Memory 相关测试全过（如存在 `test_session_memory*.py`）。
- [x] 4.5 确认无遗留 `print()` / `TODO` / 注释代码块。
- [x] 4.6 确认 `compact_conversation` 和 `SessionMemory.extract` 的外部接口签名未变。
- [x] 4.7 确认 `ContextSummary` 表结构未变。
- [x] 4.8 确认无新增第三方依赖（`transcript_renderer.py` 只导入 `compact_pipeline` 和 `model_registry`）。
