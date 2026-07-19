## Why

四层压缩系统目前有三套不同的 message-level token 估算函数，虽然各自在其上下文中是合理的，但缺乏统一的公共入口和文档化关系，导致：

1. **维护漂移风险**：Tier 0 的 `estimate_messages_tokens(list[dict])`（content-only，含 reasoning_content）和 Tier 4 的 `_estimate_chat_message_tokens(dict)`（content + tool_calls，不含 reasoning_content）逻辑几乎相同但有细微差异。没有共享函数，未来修改一处容易忘记另一处。

2. **估算基准不一致**：Tier 0 用 content-only 估算（排除 JSON 元数据，偏低 15-25%），Tier 4 也用 content-only 但函数不同。Session Memory 在 change-1 修复前用 text-only（严重偏低）。Tier 2/3 的 `_message_token_estimate` 是私有函数，change-1 会提取为 `estimate_full_message_tokens(list[Message])`。三套函数的基准（4 chars ≈ 1 token）相同，但"算哪些字段"不统一。

3. **缺乏文档化**：没有地方说明"哪个估算函数用于什么场景、为什么有差异"。新人容易误用。

## What Changes

- **提取共享的 dict-format 估算函数**：在 `transcript_renderer.py`（change-1 新建的模块）中新增 `estimate_dict_message_tokens(msg: dict) -> int`，统一 Tier 0 和 Tier 4 的 dict-format 估算逻辑。Tier 0 的 `estimate_messages_tokens` 和 Tier 4 的 `_estimate_chat_message_tokens` 都改为调用它。
- **统一 Message-format 估算**：change-1 已将 `estimate_full_message_tokens(list[Message])` 提取为公共函数。本变更确保 Tier 2/3 的 `estimate_uncompacted_tokens` 和 Session Memory 的 `should_extract` 都调用它（change-1 已做）。
- **在 `transcript_renderer.py` 顶部添加文档化注释**：说明三个估算函数的关系和使用场景。
- **删除 Tier 4 的 `_estimate_chat_message_tokens` 私有函数**：改为调用共享的 `estimate_dict_message_tokens`。

## Capabilities

### New Capabilities

（无——本变更是 change-1 和 change-2 的收尾对齐，不引入新 capability。）

### Modified Capabilities

- `transcript-rendering`：`transcript_renderer.py` 新增 `estimate_dict_message_tokens` 公共函数，供 Tier 0 和 Tier 4 复用 dict-format 估算。模块顶部添加估算函数关系文档化。
- `conversation-context`：Tier 4 的 `_estimate_chat_message_tokens` 删除，改为调用 `estimate_dict_message_tokens`。
- `run-internal-compaction`：Tier 0 的 `estimate_messages_tokens` 内部改为调用 `estimate_dict_message_tokens`（逐条累加），外部接口不变。

## Impact

- **后端**：
  - `backend/app/services/transcript_renderer.py` — 新增 `estimate_dict_message_tokens(msg: dict) -> int`。
  - `backend/app/services/compact_pipeline.py` — `estimate_messages_tokens` 内部改为 `sum(estimate_dict_message_tokens(m) for m in messages)`，外部接口不变。
  - `backend/app/services/conversation_context.py` — 删除 `_estimate_chat_message_tokens`，改为调用 `estimate_dict_message_tokens`。
- **DB / API / 事件**：无变更。
- **依赖**：无新依赖。
- **向后兼容**：
  - `estimate_messages_tokens` 的外部接口和返回值不变。
  - Tier 4 的 `_estimate_chat_message_tokens` 是私有函数，删除不影响外部调用方。
  - 估算结果可能有极微小变化（如果 `estimate_dict_message_tokens` 统一了 reasoning_content 的处理），但 Tier 4 本来就不该算 reasoning_content（spec 13 不回传 thinking）。
- **测试**：
  - `estimate_dict_message_tokens` 单元测试。
  - 回归：Tier 0 / Tier 4 的估算结果与改造前一致（或仅在 reasoning_content 处理上有文档化的差异）。
