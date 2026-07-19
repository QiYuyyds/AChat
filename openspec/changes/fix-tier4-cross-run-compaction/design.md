## Context

Tier 4（`conversation_context.py:build_history_for`）负责跨 run 的 DB messages → LLM history 序列化。它把 `messages` 表里的 Message 记录转成 OpenAI chat message dict 数组，供 adapter 的 `history` 字段消费。

### 现状的三个核心缺陷

1. **死胡同 marker**：`prune_old_tool_results` 把大 tool_result 替换成 `[tool_result 已裁剪, 详见 message_id=xxx]`。但没有 `read_message` 工具能取回 message_id 对应的内容——信息彻底丢失。Tier 0 已在 `compact_markers.py` 的 `CompactMarkerBuilder.build_tool_result_marker` 中解决了：marker 带 `recover` 字段告诉模型如何重新获取（如 `fs_list(path='src', depth=3) 重新获取结构`）。

2. **count-based fold 切断 turn**：`fold_old_messages` 用 `FOLD_THRESHOLD=30` / `FOLD_KEEP_RECENT=20`（message count）。一个 7 工具 turn = 8 条 message，保留 20 条 = 2.5 个 turn。count-based 不保证 turn 完整性——可能切到第 3 个 turn 的第 5 条 tool message，留下孤立的 tool_result（无对应 tool_use）。Tier 0 已用 `TurnBoundaryFinder` + `KEEP_RECENT_TURNS=2` 解决。

3. **无差别裁剪**：`prune_old_tool_results` 用统一 `TOOL_RESULT_PRUNE_THRESHOLD=2000` token 阈值。fs_list 的结构信息（后续 fs_read 路径推理的依据）和 bash 的输出被同等对待。Tier 0 已按 tool 类型差异化保留（fs_list 保 name+relativePath、fs_read full 转 outline、code_explore 完整保留）。

### 与 Tier 0 的断层

Tier 0（`compact_pipeline.py` / `compact_markers.py`）已在 `fix-tier0-run-internal-compaction` change 中落地了完整的解决方案：
- `ToolResultSummarizer`（`summarize_tool_result`）— 按 tool name + mode + stage 差异化保留
- `TurnBoundaryFinder`（`find_turn_boundaries` / `keep_recent_turns`）— 按 turn 边界剪裁
- `CompactMarkerBuilder`（`build_tool_result_marker` / `build_fold_marker`）— 结构化纯文字 marker

但 Tier 4 没有复用这些，仍用旧的 count-based + 死胡同 marker 方案。本变更让 Tier 4 复用 Tier 0 的公共基础设施。

### DB Message 与 in-memory dict 的差异

Tier 0 的 `find_turn_boundaries` 接收 `list[dict]`（OpenAI chat message format），Tier 4 的 messages 是 `list[Message]`（SQLAlchemy model）。需要适配：
- Message 的 `parts_list` 里的 `tool_use` part 对应 OpenAI 的 `tool_calls`
- Message 的 `parts_list` 里的 `tool_result` part 对应 OpenAI 的 `role="tool"` message
- 需要一个适配层把 Message 的 part 结构映射为 `find_turn_boundaries` 能消费的格式，或者写一个 Message-list 版本的 turn boundary finder

### 已验证的前提

DB 里 agent message 的 `parts_list` **包含 `tool_use` 和 `tool_result` part**（`persist_event` 的 `message.end` 把 `final_parts` 全量写入 DB，line 2589）。所以 turn 边界检测可以工作——agent message 里有 `tool_use` part 就标志一个 turn 开始。

## Goals / Non-Goals

**Goals:**

- `prune_old_tool_results` 改用 `TurnBoundaryFinder` 找 cutoff（末 2 完整 turn 之前），用 `ToolResultSummarizer` 按 tool 类型差异化裁剪，用 `CompactMarkerBuilder.build_tool_result_marker` 生成可恢复 marker。
- `fold_old_messages` 改用 `TurnBoundaryFinder` + `KEEP_RECENT_TURNS=2`，用 `CompactMarkerBuilder.build_fold_marker` 生成结构化 fold marker。
- `_render_agent_public_text` 的 tool_result replay 按 tool 类型差异化截断（code_explore / outline / head 不截断）。
- 消除 Tier 4 旧常量，统一用 `compact_pipeline` 的常量。

**Non-Goals:**

- 不动 Tier 0（`compact_pipeline.py` / `compact_markers.py`）——已落地。
- 不动 Session Memory / Tier 2/3——留给 `unify-transcript-renderer` change。
- 不改 `build_history_for` 的外部接口签名。
- 不改 DB schema。
- 不改 PromptAssembler 路径（`_build_history_with_assembler`）——它最终也调 `_build_history_legacy`，所以间接受益。
- 不引入 LLM 调用——Tier 4 仍是纯 read-path。

## Decisions

### D1. Message-list 版本的 turn boundary finder

- **选择**：在 `conversation_context.py` 中新增 `_find_turn_boundaries_messages(messages: list[Message]) -> list[tuple[int, int]]`，逻辑与 `compact_pipeline.find_turn_boundaries` 一致，但输入是 `list[Message]` 而非 `list[dict]`。一个 turn = 1 条 `role=="agent"` 且 `parts_list` 含 `tool_use` part 的 Message + 紧跟的所有 `role=="agent"` 且 parts_list 只含 `tool_result` part 的 Message（或同一条 Message 内既有 tool_use 又有 tool_result）。
- **理由**：
  - DB Message 的 part 结构与 OpenAI dict 不同——Message 的 `tool_use` 和 `tool_result` 都在 `parts_list` 里（可能同一条 Message），而 OpenAI dict 里 `tool_use` 在 assistant 的 `tool_calls`、`tool_result` 在独立的 `role=tool` message。
  - 不改 `compact_pipeline.find_turn_boundaries` 的签名（它接收 `list[dict]`，被 Tier 0 的 in-memory 路径使用），避免影响 Tier 0。
  - 实际上 DB 里 agent message 通常一条 Message 同时含 tool_use 和 tool_result part（`persist_event` 在 `message.end` 时一次性写入所有 parts），所以 turn 边界更简单：每条含 tool_use 的 agent Message 就是一个 turn。
- **替代方案**：把 Message 转成 dict 再调 `find_turn_boundaries`。被否决——转换开销大且不自然，DB Message 的 part 结构天然适合直接检测。

### D2. prune_old_tool_results 的 cutoff 改用 turn 边界

- **选择**：`prune_old_tool_results` 用 `_find_turn_boundaries_messages` 找到末 2 个完整 turn 的起始 index 作为 cutoff。对 cutoff 之前的 message，扫描其 `parts_list` 中的 `tool_result` part，按 tool 类型用 `summarize_tool_result(stage="tier4")` 裁剪。裁剪后的 marker 用 `CompactMarkerBuilder.build_tool_result_marker` 生成。
- **stage 参数**：新增一个 `stage="tier4"` 或直接用 `stage=1`（轻剪）——因为 Tier 4 是跨 run 的最后防线，应该保留更多语义。选择 `stage=1`。
- **理由**：
  - Tier 0 的 `stage=1` 策略已经平衡了 token 节省和信息保留。
  - 跨 run 时 agent 更需要历史语义（因为不像 in-memory 有最近的完整 turn 可参考），所以用最轻的 stage。
- **替代方案**：为 Tier 4 新建一套策略表。被否决——Tier 0 的策略表已验证，复用避免漂移。

### D3. fold_old_messages 改用 turn 边界 + 结构化 marker

- **选择**：`fold_old_messages` 用 `_find_turn_boundaries_messages` + `KEEP_RECENT_TURNS=2`（从 `compact_pipeline` 导入）。old 段用 `CompactMarkerBuilder.build_fold_marker` 生成结构化 marker（带 `tools_used` / `summary` / `first_user` / `last_reply`）。pinned messages 保护逻辑保留。
- **触发条件**：当完整 turn 数 > `FOLD_TURN_THRESHOLD=4`（从 `compact_pipeline` 导入）时才 fold。turn 数 ≤ 4 时不 fold（不够多，不值得丢信息）。
- **fallback**：如果没有完整 turn（所有 agent message 都无 tool_use），fallback 到 `LEGACY_RECENT_KEEP=6`（count-based），加 warning 日志。
- **理由**：与 Tier 0 的 `_stage3_fold` 逻辑完全一致，只是输入类型不同。

### D4. tool_result replay 差异化截断

- **选择**：`_render_agent_public_text` 中的 `TOOL_RESULT_REPLAY_CHAR_CAP=4000` 统一截断改为按 tool 类型差异化：
  - `code_explore`：不截断（高密度摘要，截断等于浪费）
  - `fs_read(mode=outline/head)`：不截断（本身就短）
  - 其他：保持 4000 字符上限
- **实现**：需要从 tool_result part 的 `callId` 反查同消息的 `tool_use` part 获取 `toolName` 和 `args.mode`。
- **理由**：Tier 0 的策略表已经把 code_explore 列为"永远完整保留"。Tier 4 的 replay 是给跨 run agent 看的，更不应该截断 code_explore。

### D5. 保留 _find_safe_cut_point 的 tool 配对保护

- **选择**：Tier 2/3 的 `_find_safe_cut_point` 已有 `_is_orphan_tool_result` / `_is_pending_tool_use` 保护（防止 cut 切断 tool_use/tool_result 配对）。Tier 4 的 `prune_old_tool_results` 不做 cut（只替换 part），所以不需要配对保护。但 `fold_old_messages` 做 cut（丢弃 old 段），需要配对保护。
- **实现**：`fold_old_messages` 的 turn 边界天然保证配对完整（一个 turn = tool_use + 所有 tool_result），所以不需要额外的 `_is_orphan_tool_result` 检查。但如果 turn 边界检测失败（fallback 到 count-based），需要复用 `_find_safe_cut_point` 的配对保护逻辑。
- **理由**：turn 边界是比配对保护更强的保证——它保证整个 turn 完整，不只是配对完整。

## Risks / Trade-offs

- **[Message-list turn finder 与 dict-list turn finder 逻辑漂移]** → 缓解：两者逻辑一致，只是输入类型不同。单元测试用相同的数据结构（转为 Message 和 dict 两种形式）验证结果一致。

- **[Tier 4 fold 后 token 可能不减]** → 缓解：Tier 4 不做 success 判定（它是 read-path，不像 Tier 0 有 `CompactSuccessJudge`）。fold 的目的是减少 message 数量（old 段合并为 1 条 marker），token 减少是副产品。如果 fold 后 token 反而增加（marker 比 old 段还大），`CompactMarkerBuilder` 的 500 字符硬上限保证 marker 不会爆炸。

- **[老 marker 不被二次处理]** → 之前生成的 `[tool_result 已裁剪]` marker 已经是 `type=text` part，`prune_old_tool_results` 只匹配 `type=tool_result`，所以不会二次处理。这是预期行为——老 marker 会自然随着新 fold 被丢弃。

- **[PromptAssembler 路径的影响]** → `_build_history_with_assembler` 最终调 `_build_history_legacy`，所以间接受益。不需要单独改 PromptAssembler 路径。

## Migration Plan

无 DB 迁移、无 API 变更。纯后端 Python 实现：

1. **新增辅助函数**：在 `conversation_context.py` 中新增 `_find_turn_boundaries_messages`。
2. **重写 `prune_old_tool_results`**：用 turn 边界 + 策略表 + 结构化 marker。
3. **重写 `fold_old_messages`**：用 turn 边界 + 结构化 fold marker。
4. **修改 `_render_agent_public_text`**：tool_result replay 差异化截断。
5. **删除旧常量**：`TOOL_RESULT_PRUNE_THRESHOLD` / `TOOL_RESULT_RECENT_TURNS` / `FOLD_THRESHOLD` / `FOLD_KEEP_RECENT`，导入 `compact_pipeline` 的常量。
6. **测试覆盖**：turn 边界裁剪、结构化 marker、差异化截断、pinned 保护、回归。
7. **回滚策略**：如果生产环境发现回归，可还原 `prune_old_tool_results` 和 `fold_old_messages` 的实现。由于不改外部接口，回滚是纯内部替换。

## Open Questions

1. **`prune_old_tool_results` 的 `model` 参数是否还需要？** 当前签名是 `prune_old_tool_results(messages, model=None, recent_turns=..., prune_threshold=...)`。`model` 参数当前未使用（遗留），改用 turn 边界后 `recent_turns` 和 `prune_threshold` 也不再需要。可以简化签名为 `prune_old_tool_results(messages, keep_recent_turns=KEEP_RECENT_TURNS)`。但需要检查是否有外部调用方传了这些参数。

2. **`fold_old_messages` 的 `pinned_ids` 参数是否与 turn 边界冲突？** pinned messages 不受 fold 影响（保护逻辑保留）。但如果一条 pinned message 在 old 段的中间位置，turn 边界可能被它打断。需要确认：pinned message 是否参与 turn 边界检测？→ 答案：pinned message 参与检测（它是 Message 列表的一部分），但 fold 时被保护不被丢弃。如果 pinned message 恰好在 turn 边界上，它会被保留在 kept_from_old 中。
