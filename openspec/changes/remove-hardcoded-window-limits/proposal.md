## Why

AChat 的短期记忆滑动窗口使用两个硬编码绝对数字（`DEFAULT_MAX_TURNS = 20` 和 `AUTO_COMPACT_WATERMARK = 30`）限制消息加载和压缩触发。这些数字在 200K context 时代合理，但在 1M context 模型（如 DeepSeek V4）下导致两个问题：DB 查询只加载 20 条消息浪费 90% 上下文空间；30 条消息只占 15% 上下文却过早触发 LLM 压缩。ratio-aware 裁剪（0.65 阈值）已实现比例驱动的裁剪决策，但加载层和压缩触发层仍是绝对数字，形成失配。

## What Changes

- 移除 `conversation_context.py` 中的 `DEFAULT_MAX_TURNS = 20` 常量及 DB 查询的 `.limit(max_turns)` 调用，跨 run 历史加载不再受条数限制，改为全量加载 uncompacted 消息后由 token budget 兜底裁剪
- 移除 `context_compaction_service.py` 中的 `AUTO_COMPACT_WATERMARK = 30` 常量，auto-compact hook 不再使用消息条数触发
- 简化 `_maybe_auto_compact_hook`：删除消息数 trigger 分支，只保留 87% token trigger 作为唯一 auto-compact 触发条件
- **BREAKING**：`BuildHistoryOptions.max_turns` 字段语义变更——默认 `None` 表示不限制加载条数（原为 20），外部调用方显式传入时仍生效
- **BREAKING**：`agent_id` 缺失时 auto-compact 不再触发（原由 30 条消息数 trigger 兜底），正常 SDK agent run 不受影响

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `conversation-context`: 历史加载从固定条数限制改为 token-budget 驱动；auto-compact 触发从消息数 + token 双条件改为仅 token 条件

## Impact

- **后端代码**：`conversation_context.py`（删除 `DEFAULT_MAX_TURNS`、`.limit()` 调用）、`context_compaction_service.py`（删除 `AUTO_COMPACT_WATERMARK`）、`agent_runner.py`（简化 `_maybe_auto_compact_hook`、清理 import）
- **后端测试**：`test_auto_compact_hook.py`（删除消息数 trigger 测试、增强 87% token trigger 测试）、`test_ratio_aware_pruning.py`（新增无 LIMIT 全量加载测试）
- **前端**：不受影响。`UsageBadge` 的 `inputTokens` 来自 LLM API 返回值，会自然反映加载更多历史后的真实用量，无需改动
- **SSE 事件**：不受影响。不新增/修改/删除任何事件类型
- **性能**：`estimate_uncompacted_tokens` 每次 run 结束后执行（原先被 30 条消息数 short-circuit），但 SQLite 本地查询 174 条 < 10ms，后台任务不在关键路径
