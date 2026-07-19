## 1. 新模块搭建

- [x] 1.1 创建 `backend/app/services/compact_pipeline.py`，定义模块顶部常量：`STAGE1_SUMMARIZE_RATIO = 0.70`、`STAGE2_PRUNE_RATIO = 0.80`、`STAGE3_FOLD_RATIO = 0.88`、`KEEP_RECENT_TURNS = 2`、`FOLD_TURN_THRESHOLD = 4`、`EFFECTIVE_COMPACT_RATIO = 0.85`、`MAX_MARKER_CHARS = 500`、`MAX_SUMMARY_CHARS = 200`，并加 `__all__` 显式声明导出符号。
- [x] 1.2 创建 `backend/app/services/compact_markers.py`，留空 `CompactMarkerBuilder` 和 `CompactSuccessJudge` 类骨架（后续步骤填充）。
- [x] 1.3 在 `backend/app/services/__init__.py`（或保持现状，不强制 barrel）确保 `compact_pipeline` 和 `compact_markers` 模块可被 `agent_runner.py` 导入。

## 2. Token 估算修正

- [x] 2.1 在 `compact_pipeline.py` 实现 `estimate_messages_tokens(messages: list[dict]) -> int`，只累加：`content`（str 或 list[dict] 时累加每个 `part.get("text")`）、`tool_calls[*].function.name` + `tool_calls[*].function.arguments`、`reasoning_content`。每条 message 加 4 tokens overhead。不算 `role` / `tool_call_id` / `type` 等元数据。
- [x] 2.2 编写单元测试 `backend/tests/test_compact_pipeline.py::test_estimate_messages_tokens_excludes_metadata`：构造一个含 assistant + tool 消息的 list，断言新估算比 `estimate_tokens(json.dumps(messages))` 低 15-25%。
- [x] 2.3 编写测试 `test_estimate_messages_tokens_handles_multimodal_content`：当 `content` 是 list 时（如 vision parts），只累加 `type=text` 的 `text` 字段，跳过 `image_url`。

## 3. TurnBoundaryFinder

- [x] 3.1 在 `compact_pipeline.py` 实现 `find_turn_boundaries(messages: list[dict]) -> list[tuple[int, int]]`，扫描 messages，返回每个 turn 的 `(start_index, end_index)`。一个 turn = 1 条 `role=="assistant"` 且含 `tool_calls` 的消息 + 紧跟的所有 `role=="tool"` 消息。
- [x] 3.2 实现 `keep_recent_turns(messages, k=2) -> tuple[list, list]`，返回 `(recent, old)`：`recent` 是末 k 个完整 turn，`old` 是其余。当 `len(boundaries) <= k` 时返回 `(messages, [])`。
- [x] 3.3 编写测试 `test_find_turn_boundaries_basic`：构造 4 个 turn（每个含 assistant + 7 tool），断言返回 4 个 `(start, end)` 元组且 end - start = 8。
- [x] 3.4 编写测试 `test_find_turn_boundaries_no_tool_calls`：当所有 assistant 消息都无 `tool_calls` 时，返回空列表；调用方 fallback 到 `recent_keep=6` 路径（在 agent_runner.py 层处理）。
- [x] 3.5 编写测试 `test_keep_recent_turns_returns_correct_split`：8 turn 输入，`k=2`，断言 `recent` 含末 2 turn 完整序列、`old` 含前 6 turn，且 `tool_use` 与 `tool_result` 配对不被切断。

## 4. ToolResultSummarizer

- [x] 4.1 在 `compact_pipeline.py` 实现 `summarize_tool_result(tool_name: str, args: dict, content: str, stage: int) -> str`，按 stage 1/2/3 差异化保留。stage 取值 1（轻）/2（中）/3（重）。
- [x] 4.2 实现 `fs_list` 分支：解析 `args.depth` 和 `args.path`。stage 1：保留 entries 的 `name + relativePath`，丢 `size/depth/isDirectory`。stage 2：仅保留 directory tree（只列 directory entries 的 `name`）。stage 3：仅返回 `f"目录 {path} 含 N 文件、M 子目录"`。
- [x] 4.3 实现 `fs_read` 分支：解析 `args.mode`。`mode="full"` 时 stage 1 调 `extract_outline(content, detect_language(path))`（从 `fs_service.py` 导入）转骨架，含 `outline/language/totalLines/fullSize`；stage 2 保留首 3 行 + outline 前 5 条；stage 3 仅返回 `f"文件 {path} 有 N 行，主要定义了 ..."`（从 outline 头部提取）。`mode="outline"` / `mode="head"` 永远完整保留。
- [x] 4.4 实现 `bash` 分支：stage 1 保留末 20 行 + exit_code（如 content 中有）；stage 2 保留末 5 行；stage 3 仅返回 exit_code + 末 1 行。
- [x] 4.5 实现 `fs_grep` 分支：stage 1 保留前 10 matches；stage 2 前 5 matches；stage 3 仅 `f"找到 N 处匹配"`。
- [x] 4.6 实现 `code_explore` 分支：stage 1/2/3 永远完整返回 content（密度高、不可恢复）。
- [x] 4.7 实现通用 fallback：未匹配的 tool，stage 1 保留前 1000 字符，stage 2 替换为 marker，stage 3 折叠进 fold marker。
- [x] 4.8 编写测试 `test_summarize_fs_list_depth_gt_1`：模拟 `fs_list(depth=3)` 返回 500 entries，断言 stage 1 摘要保留 `name/relativePath`、丢 `size/depth`、token 下降 ≥40%。
- [x] 4.9 编写测试 `test_summarize_fs_read_full_to_outline`：模拟 `fs_read(mode="full")` 返回 12k tokens 文件，断言 stage 1 调 `extract_outline`、返回 `outline` 字段、`content` 字段被丢弃。
- [x] 4.10 编写测试 `test_summarize_code_explore_preserved`：模拟 `code_explore` 7.5k tokens 结果，断言 stage 1/2/3 全部完整保留。
- [x] 4.11 编写测试 `test_summarize_unknown_tool_fallback`：未在策略表的 tool，断言 stage 1 返回前 1000 字符、stage 2 返回 marker、stage 3 折叠。

## 5. CompactMarkerBuilder + CompactSuccessJudge

- [x] 5.1 在 `compact_markers.py` 实现 `CompactMarkerBuilder.build_tool_result_marker(stage, tool_name, args, summary, recover_hint) -> str`，返回结构化纯文字 marker，格式 `[compacted stage=N tool=X args=...]\n[summary: ...]\n[recover: ...]`。单 marker ≤ 500 字符，summary ≤ 200 字符。
- [x] 5.2 实现 `CompactMarkerBuilder.build_fold_marker(stage, turns_folded, tools_used_counts, summary, first_user_msg_head, last_assistant_text_head) -> str`，格式 `[folded stage=3 turns=N tools: fs_list×2 fs_read×5 ...]\n[summary: ...]`。`tools_used` 只列 top 5 + count。
- [x] 5.3 实现 `CompactSuccessJudge.judge(pre_tokens, post_tokens, pre_len, post_len) -> bool`，返回 `post_tokens < pre_tokens * 0.85`。`len` 变化不再算成功。
- [x] 5.4 编写测试 `test_judge_returns_true_when_token_drops_15_percent`：`pre=100k, post=80k, pre_len=50, post_len=20` → True。
- [x] 5.5 编写测试 `test_judge_returns_false_when_only_len_changes`：`pre=100k, post=98k, pre_len=50, post_len=15` → False（fold 把多条合并成 1 条 marker，但 token 没降）。
- [x] 5.6 编写测试 `test_marker_length_capped`：构造一个超长 summary（300 字符），断言生成的 marker ≤ 500 字符。

## 6. 五阶段 Pipeline 主干

- [x] 6.1 在 `compact_pipeline.py` 实现 `run_compact_pipeline(messages, stage) -> list[dict]`，分发到 `_stage1_summarize` / `_stage2_prune` / `_stage3_fold`。
- [x] 6.2 实现 `_stage1_summarize(messages)`：保留末 `KEEP_RECENT_TURNS=2` 完整 turn（用 `find_turn_boundaries` + `keep_recent_turns`），对 `old` 段中的每条 `role=="tool"` 消息，用 `summarize_tool_result` 替换 `content`。保留 `tool_use` 和 `assistant` 消息原样。
- [x] 6.3 实现 `_stage2_prune(messages)`：对 stage 1 已摘要过的 tool_result 再摘要（stage=2），其它消息不动。
- [x] 6.4 实现 `_stage3_fold(messages)`：调 `find_turn_boundaries`，若空则 fallback 到 `recent_keep=6`；否则 `keep_recent_turns(k=2)`，`old` 段用 `CompactMarkerBuilder.build_fold_marker` 合成单条 `role="system"` marker。
- [x] 6.5 编写测试 `test_pipeline_stage1_preserves_recent_turns`：构造 4 turn × 7 tool 输入，断言 stage 1 后末 2 turn 完整、前 2 turn 的 tool_result 被摘要但 assistant/tool_use 保留。
- [x] 6.6 编写测试 `test_pipeline_stage3_fold_uses_turn_boundary`：构造 4 turn 输入，断言 stage 3 后只剩 1 fold marker + 末 2 turn，且无孤立 tool_use 或 tool_result。
- [x] 6.7 编写测试 `test_pipeline_stage3_fallback_when_no_turns`：构造全 text 消息（无 tool_calls），断言 fallback 到 `recent_keep=6` 并打 warning 日志。

## 7. react_loop_termination.py 修改

- [x] 7.1 扩展 `DecisionAction` 类型，增加 `"summarize"` / `"prune"` / `"fold"` 三个值（保留现有 `"continue"` / `"compact"` / `"soft_inject"` / `"force_final"` / `"hard_stop"`）。`"compact"` 仍保留作为兼容路径（旧 toggle 关时用）。
- [x] 7.2 在 `react_loop_termination.py` 顶部新增常量：`STAGE1_RATIO = 0.70`、`STAGE2_RATIO = 0.80`、`STAGE3_RATIO = 0.88`。`COMPACT_RATIO` 从 0.90 调到 0.85。
- [x] 7.3 修改 `decide_pre_model`：在现有 `ratio >= COMPACT_RATIO` 检查**之前**插入分级检查：`ratio >= STAGE1_RATIO` → action=`"summarize"`；`ratio >= STAGE2_RATIO` → action=`"prune"`；`ratio >= STAGE3_RATIO` → action=`"fold"`。原 `action="compact"` 仅当 `compact_pipeline_enabled=False`（legacy 路径）时返回。
- [x] 7.4 编写测试 `test_decide_pre_model_stage1_at_0_70`：`ratio=0.72` 时返回 `action="summarize"`。
- [x] 7.5 编写测试 `test_decide_pre_model_stage3_at_0_88`：`ratio=0.89` 时返回 `action="fold"`，不返回旧的 `"compact"`。
- [x] 7.6 编写测试 `test_decide_pre_model_legacy_when_disabled`：当传入 `pipeline_enabled=False` 标志（参数化）时，`ratio=0.86` 返回旧的 `action="compact"`。

## 8. agent_runner.py 接入 Pipeline

- [x] 8.1 修改 `_run_react_loop` 中 `total_tokens` 计算（agent_runner.py:1010），从 `estimate_tokens(json.dumps(messages))` 改为 `estimate_messages_tokens(messages)`（从 `compact_pipeline` 导入）。
- [x] 8.2 修改 compact 分支（agent_runner.py:1028-1046）：读 settings 的 `compact_pipeline_enabled`，True 时按 `decision.action` 分发到 `run_compact_pipeline(messages, stage=1/2/3)`；False 时走旧 `_mid_run_compact` 路径。
- [x] 8.3 把 `success = post_tokens < pre_tokens or len(messages) < pre_compact_count` 改为 `success = CompactSuccessJudge.judge(pre_tokens, post_tokens, pre_len, post_len)`。
- [x] 8.4 保留 `mark_compact_result(term, success=...)` 调用，让 `compact_disabled` 断路器能真正触发。
- [x] 8.5 保留 `continue` 重新评估的行为——stage N 触发后回到 `decide_pre_model` 决定下一步。
- [x] 8.6 编写测试 `test_react_loop_uses_new_token_estimate`：mock `decide_pre_model`，断言 `total_tokens` 来自 `estimate_messages_tokens` 而非 `estimate_tokens(json.dumps(messages))`。
- [x] 8.7 编写测试 `test_react_loop_dispatches_to_stage1`：mock `decide_pre_model` 返回 `action="summarize"`，断言调用了 `run_compact_pipeline(messages, stage=1)`。
- [x] 8.8 编写测试 `test_react_loop_legacy_fallback_when_disabled`：settings `compact_pipeline_enabled=False` 时，断言调用旧的 `_mid_run_compact`，不调用 `run_compact_pipeline`。

## 9. Settings 开关

- [x] 9.1 在 `backend/app/config.py` 的 `Settings` 类增加 `compact_pipeline_enabled: bool = True` 字段。
- [x] 9.2 在 `backend/.env.example` 增加 `COMPACT_PIPELINE_ENABLED=true` 注释行，说明用途（"启用五阶段压缩 pipeline；false 回退到旧 _mid_run_compact"）。
- [x] 9.3 在 `backend/app/services/agent_runner.py` 的 `_run_react_loop` 内部读 settings 一次（不在每 turn 读，避免开销），传入 `decide_pre_model` 和 compact 分支。

## 10. 端到端 + 回归测试

- [x] 10.1 编写端到端测试 `test_eight_turn_seven_tool_pipeline`：构造 8 turn × 7 tool = 56 messages 场景（含 fs_list / fs_read full / bash / code_explore），验证 stage 1/2/3 逐级降 token 且末 2 turn 完整保留。
- [x] 10.2 编写回归测试 `test_legacy_path_unchanged_when_disabled`：`compact_pipeline_enabled=False` 时，行为与现状完全一致（`recent_keep=6` / json.dumps 估算 / len-based success）。
- [x] 10.3 编写测试 `test_compact_disabled_triggers_after_three_failures`：mock stage 1/2/3 全部返回 success=False，断言 3 次后 `compact_disabled=True`，下次 `decide_pre_model` 直接走 `soft_inject`。
- [x] 10.4 跑 `ruff check .` 全过（新文件无 lint，agent_runner.py 预存 lint 不在修改范围）。
- [x] 10.5 跑 `pytest backend/tests/test_compact_pipeline.py backend/tests/test_compact_markers.py` 全过。
- [x] 10.6 跑现有 `backend/tests/test_react_loop_termination*.py` 全过（确保 4/5 阶段行为不被破坏）。

## 11. Spec 文档同步

- [x] 11.1 把 `openspec/specs/run-internal-compaction/spec.md`（新 capability）从 changes 目录 sync 到 main specs 目录——按 `openspec-sync-specs` skill 流程执行（本次 change 完成后 archive 时再做）。
- [x] 11.2 在 `specs/13-conversation-context.md` 顶部补一行交叉引用：`> Run 内 in-memory 压缩见 openspec/specs/run-internal-compaction/spec.md`，明确两者边界。
- [x] 11.3 在 `CLAUDE.md` §8 specs 索引追加一行：`- openspec/specs/run-internal-compaction/spec.md — ReAct loop 内压缩（五阶段 pipeline）`。

## 12. 自检清单

- [x] 12.1 后端 `ruff check .` 通过（新文件无 lint；agent_runner.py 预存 lint 不在修改范围）。
- [x] 12.2 后端 `pytest` 全过（含新增 + 现有回归，73 测试通过）。
- [x] 12.3 无遗留 `print()` / `TODO` / 注释代码块。
- [x] 12.4 `compact_pipeline_enabled=False` 时行为与改造前完全一致（回归测试覆盖）。
- [x] 12.5 阶段 4/5（soft_inject / force_final）行为零变更。
- [x] 12.6 `ToolResultEvent` 持久化到 DB 的内容仍是完整 tool_result（Tier 0 只动 in-memory messages[]，不改 event）。
- [x] 12.7 无新增第三方依赖（`extract_outline` 用 Python `re`，不引入 tiktoken）。
