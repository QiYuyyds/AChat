## Why

Tier 4（`conversation_context.py:build_history_for`）负责跨 run 的 DB messages → LLM history 序列化。它有两个退化问题，与 Tier 0 已修复的方案形成断层：

1. **Marker 退化**：`prune_old_tool_results` 用死胡同 marker `[tool_result 已裁剪, 详见 message_id=xxx]`——但没有 `read_message` 工具能取回，信息彻底丢失。`fold_old_messages` 的 marker `[已折叠 N 条消息 (时间 range)]` 不含 `tools_used` / `summary` / `recover` 任何元信息。Tier 0 已在 `fix-tier0-run-internal-compaction` 中把 marker 升级为结构化纯文字（带 `stage` / `tool` / `summary` / `recover`），但 Tier 4 没有跟进。

2. **Cut 策略退化**：`fold_old_messages` 用 `FOLD_THRESHOLD=30` / `FOLD_KEEP_RECENT=20`（count-based），不是按 turn 边界切。这正是 Tier 0 D3 决策已经否决的方案——count-based 会切断 `tool_use ↔ tool_result` 配对。`prune_old_tool_results` 用统一 token 阈值（2000）无差别裁剪，不区分 fs_list 结构数据和 bash 输出。Tier 0 已按 tool 类型差异化保留，Tier 4 没有跟进。

**后果**：跨 run 时 agent 看到的历史里，老 tool_result 全是死胡同 marker，老 turn 被 count-based 折叠切断。agent 失忆后重新探索 → 幻觉 → context 快速耗尽。

## What Changes

- **`prune_old_tool_results` 改造**：用 `TurnBoundaryFinder` 找到末 2 个完整 turn 的边界作为 cutoff，替代 `recent_turns=3`（message count）。对 cutoff 之前的 `tool_result` part，按 tool 类型用 Tier 0 的 `ToolResultSummarizer` 策略表差异化裁剪（替代统一 2000 token 阈值），替换 marker 改用 `CompactMarkerBuilder.build_tool_result_marker`（带 `summary` + `recover`）。
- **`fold_old_messages` 改造**：用 `TurnBoundaryFinder` + `KEEP_RECENT_TURNS=2` 替代 `FOLD_THRESHOLD=30` / `FOLD_KEEP_RECENT=20`（count-based）。fold marker 改用 `CompactMarkerBuilder.build_fold_marker`（带 `tools_used` + `summary` + `first_user` + `last_reply`）。
- **`_render_agent_public_text` 的 tool_result replay 差异化**：`code_explore` 结果不截断（当前 `TOOL_RESULT_REPLAY_CHAR_CAP=4000` 统一截断），`fs_read(mode=outline/head)` 也不截断。其他 tool_result 保持 4000 字符上限。
- **删除 Tier 4 旧常量**：`TOOL_RESULT_PRUNE_THRESHOLD` / `TOOL_RESULT_RECENT_TURNS` / `FOLD_THRESHOLD` / `FOLD_KEEP_RECENT`，改为复用 `compact_pipeline` 的 `KEEP_RECENT_TURNS` 等。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `conversation-context`: `build_history_for` 的 `prune_old_tool_results` 和 `fold_old_messages` 从 count-based + 死胡同 marker 升级为 turn-based + 结构化可恢复 marker，复用 Tier 0 的 `ToolResultSummarizer` / `TurnBoundaryFinder` / `CompactMarkerBuilder`。tool_result replay 按 tool 类型差异化截断。外部接口 `build_history_for` 签名不变。

## Impact

- **后端**：
  - `backend/app/services/conversation_context.py` — 重写 `prune_old_tool_results`（用 turn 边界 + 策略表 + 结构化 marker）；重写 `fold_old_messages`（用 turn 边界 + 结构化 fold marker）；修改 `_render_agent_public_text` 的 tool_result replay（差异化截断）；删除旧常量，导入 `compact_pipeline` / `compact_markers` 的公共符号。
  - `backend/app/services/compact_pipeline.py` — 可能需要导出 `find_turn_boundaries` 的 Message-list 适配版本（当前接收 `list[dict]`，Tier 4 的是 `list[Message]`）。
  - `backend/app/services/compact_markers.py` — 无改动（`CompactMarkerBuilder` 已是公共 API）。
- **DB / API / 事件**：无变更。Tier 4 仍是 read-path，不写 DB。`build_history_for` 返回的 `list[ChatMessage]` 格式不变（OpenAI chat message dict）。
- **依赖**：无新第三方依赖。复用 `compact_pipeline` / `compact_markers`（已存在）。
- **向后兼容**：
  - `build_history_for` 的调用方无感知（签名不变，返回格式不变）。
  - 历史消息中老的 marker（之前生成的 `[tool_result 已裁剪]`）不会被二次处理——它们已经是 `type=text` 的 part，不会被 `prune_old_tool_results` 匹配到（只匹配 `type=tool_result`）。
  - Pinned messages 不受影响（`fold_old_messages` 已有 pinned 保护逻辑，保留）。
- **测试**：
  - `prune_old_tool_results` 的 turn 边界裁剪测试（含 tool_use/tool_result 配对保护）。
  - `fold_old_messages` 的 turn 边界折叠测试（含 pinned 保护）。
  - marker 格式验证（含 `summary` / `recover` / `tools_used`）。
  - tool_result replay 差异化截断测试（code_explore 不截断、bash 截断）。
  - 回归：现有 `test_conversation_context*.py` 全过。
