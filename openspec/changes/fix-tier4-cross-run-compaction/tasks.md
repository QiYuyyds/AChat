## 1. Message-list turn boundary finder

- [x] 1.1 在 `backend/app/services/conversation_context.py` 中新增 `_find_turn_boundaries_messages(messages: list[Message]) -> list[tuple[int, int]]`：扫描 messages，一个 turn = 1 条 `role=="agent"` 且 `parts_list` 含 `type=="tool_use"` part 的 Message。turn 的 end_index = 该 Message 的 index（因为 DB 里 tool_use 和 tool_result 通常在同一条 Message 的 parts_list 里）。返回 `(start_index, end_index)` 列表。无 tool_use 的 agent message 不构成 turn。
- [x] 1.2 新增 `_keep_recent_turns_messages(messages, k=KEEP_RECENT_TURNS) -> tuple[list[Message], list[Message]]`：用 `_find_turn_boundaries_messages` 找到边界，返回 `(recent, old)`。`len(boundaries) <= k` 时返回 `(messages, [])`。
- [x] 1.3 编写测试 `test_find_turn_boundaries_messages_basic`：构造 4 条含 tool_use part 的 agent Message，断言返回 4 个 `(start, end)` 元组。
- [x] 1.4 编写测试 `test_find_turn_boundaries_messages_no_tool_use`：构造全 text part 的 agent Message，断言返回空列表。
- [x] 1.5 编写测试 `test_keep_recent_turns_messages_split`：构造 6 个 turn，`k=2`，断言 `recent` 含末 2 turn、`old` 含前 4 turn。

## 2. prune_old_tool_results 改造

- [x] 2.1 在 `conversation_context.py` 顶部导入 `from app.services.compact_pipeline import summarize_tool_result, KEEP_RECENT_TURNS, FOLD_TURN_THRESHOLD, LEGACY_RECENT_KEEP` 和 `from app.services.compact_markers import CompactMarkerBuilder`。
- [x] 2.2 删除旧常量 `TOOL_RESULT_PRUNE_THRESHOLD` 和 `TOOL_RESULT_RECENT_TURNS`。
- [x] 2.3 重写 `prune_old_tool_results(messages, keep_recent_turns=KEEP_RECENT_TURNS)`：用 `_keep_recent_turns_messages` 找到 cutoff（`len(old)` 的位置）。对 `old` 段中的每条 Message，扫描其 `parts_list`：对 `type=="tool_result"` 的 part，从同消息的 `tool_use` part 中按 `callId` 匹配找到 `toolName` 和 `args`；如果 tool_name 是 `code_explore` 或 `fs_read` 且 mode 是 `outline`/`head`，跳过（完整保留）；否则调 `summarize_tool_result(tool_name, args, content, stage=1)` 获取压缩内容，再用 `CompactMarkerBuilder.build_tool_result_marker(stage=1, tool_name=tool_name, args=args, summary=..., recover_hint=...)` 生成 marker，替换该 part 为 `{"type": "text", "content": marker}`。
- [x] 2.4 编写测试 `test_prune_uses_turn_boundary`：构造 4 turn（每 turn 含 tool_use + tool_result），`keep_recent_turns=2`，断言末 2 turn 的 tool_result 完整保留、前 2 turn 的 tool_result 被替换为 marker。
- [x] 2.5 编写测试 `test_prune_marker_has_recover_hint`：构造 `fs_list(depth=3)` 的 tool_result 在 old 段，断言替换后的 marker 包含 `recover` 字段和 `fs_list(path=..., depth=3)` 提示。
- [x] 2.6 编写测试 `test_prune_preserves_code_explore`：构造 `code_explore` 的 tool_result 在 old 段，断言不被替换。
- [x] 2.7 编写测试 `test_prune_preserves_fs_read_outline`：构造 `fs_read(mode="outline")` 的 tool_result 在 old 段，断言不被替换。
- [x] 2.8 编写测试 `test_prune_marker_under_500_chars`：构造各种 tool_result，断言生成的 marker 均 ≤ 500 字符。

## 3. fold_old_messages 改造

- [x] 3.1 重写 `fold_old_messages(messages, pinned_ids=None)`：用 `_find_turn_boundaries_messages` 检测 turn 数。如果 turn 数 > `FOLD_TURN_THRESHOLD=4`，用 `_keep_recent_turns_messages(k=KEEP_RECENT_TURNS)` 分割。old 段中 pinned messages 保护（不 fold），其余用 `CompactMarkerBuilder.build_fold_marker` 合并为单条 marker。如果无完整 turn（boundaries 为空），fallback 到 `LEGACY_RECENT_KEEP=6`（count-based），加 warning 日志。
- [x] 3.2 在 fold marker 生成时，收集 old 段的 `tools_used_counts`（Counter）、`first_user_head`、`last_assistant_text_head`。可复用 `compact_pipeline._collect_tool_names_in_span` / `_first_user_head` / `_last_assistant_text_head` 的逻辑，但需要适配 Message 类型（这些函数当前接收 `list[dict]`）。在 `conversation_context.py` 中写 Message 版本的辅助函数。
- [x] 3.3 删除旧常量 `FOLD_THRESHOLD` 和 `FOLD_KEEP_RECENT`。
- [x] 3.4 编写测试 `test_fold_uses_turn_boundary`：构造 6 turn，断言 fold 后只剩 1 fold marker + 末 2 turn + pinned。
- [x] 3.5 编写测试 `test_fold_marker_has_tools_used`：构造含 `fs_list×2 fs_read×5 bash×1` 的 old 段，断言 fold marker 包含 `tools: fs_read×5 fs_list×2 bash×1`（按 count 降序 top 5）。
- [x] 3.6 编写测试 `test_fold_marker_has_summary`：断言 fold marker 包含 `summary` 字段且 ≤ 200 字符。
- [x] 3.7 编写测试 `test_fold_preserves_pinned`：构造 pinned message 在 old 段，断言被保留且不被 fold。
- [x] 3.8 编写测试 `test_fold_fallback_when_no_turns`：构造全 text message（无 tool_use），断言 fallback 到 `LEGACY_RECENT_KEEP=6` 并打 warning。
- [x] 3.9 编写测试 `test_fold_no_op_when_turns_below_threshold`：构造 3 turn（< `FOLD_TURN_THRESHOLD=4`），断言不 fold，返回原列表。

## 4. tool_result replay 差异化截断

- [x] 4.1 在 `_render_agent_public_text` 中，修改 `tool_result` 分支：从同消息的 `tool_use` part 中按 `callId` 匹配找到 `toolName` 和 `args.mode`。如果 `toolName == "code_explore"` 或 (`toolName == "fs_read"` 且 `mode in ("outline", "head")`)，不截断（完整渲染）。否则保持 `TOOL_RESULT_REPLAY_CHAR_CAP=4000` 截断逻辑。
- [x] 4.2 编写测试 `test_replay_code_explore_not_truncated`：构造 8000 字符的 code_explore tool_result，断言 replay 文本完整包含 8000 字符。
- [x] 4.3 编写测试 `test_replay_fs_read_outline_not_truncated`：构造 `fs_read(mode="outline")` 的 tool_result，断言不截断。
- [x] 4.4 编写测试 `test_replay_bash_truncated`：构造 6000 字符的 bash tool_result，断言截断到 4000 + `[truncated]` 后缀。
- [x] 4.5 编写测试 `test_replay_unknown_tool_truncated`：构造未匹配 tool_use 的 tool_result，断言走默认截断逻辑。

## 5. 回归与自检

- [x] 5.1 运行 `ruff check backend/app/services/conversation_context.py` 全过。
- [x] 5.2 运行 `pytest backend/tests/test_conversation_context*.py` 全过（含新增 + 现有回归）。
- [x] 5.3 运行 `pytest backend/tests/test_tool_result_replay.py` 全过（之前修复的 field mismatch 测试仍通过）。
- [x] 5.4 确认 `build_history_for` 的外部接口签名未变。
- [x] 5.5 确认 pinned messages 保护逻辑仍有效。
- [x] 5.6 确认无遗留 `print()` / `TODO` / 注释代码块。
- [x] 5.7 确认无新增第三方依赖。
- [x] 5.8 确认 `_build_history_with_assembler` 路径间接受益（它调 `_build_history_legacy`）。
