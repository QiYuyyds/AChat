## Context

AChat 的四层压缩系统有三套 message-level token 估算函数，各自服务于不同的输入格式和上下文：

| 层 | 函数 | 输入格式 | 算什么 | 不算什么 |
|---|---|---|---|---|
| Tier 0 | `compact_pipeline.estimate_messages_tokens(list[dict])` | OpenAI dict | content + tool_calls.function + reasoning_content + 4/msg | role, tool_call_id, type |
| Tier 2/3 + Session Memory | `transcript_renderer.estimate_full_message_tokens(list[Message])`（change-1 新建） | DB Message | text + thinking + tool_use args + tool_result | role, agent_id, created_at |
| Tier 4 | `conversation_context._estimate_chat_message_tokens(dict)` | OpenAI dict（已序列化） | content + tool_calls.function + 4/msg | role, tool_call_id, type, reasoning_content |

三者的基准相同（`estimate_tokens(text) = ceil(len(text) / 4)`，来自 `model_registry.py`），但"算哪些字段"有细微差异。

### 差异的合理性

- **Tier 0 算 reasoning_content，Tier 4 不算**：这是正确的。Tier 0 是 in-memory 的 ReAct loop，messages 里有 reasoning_content（模型的 thinking）。Tier 4 是跨 run 的 DB → LLM history，spec 13 明确"thinking/reasoning_content 不回传"，所以序列化后的 dict 里没有 reasoning_content。
- **Tier 2/3 算 thinking + tool_result，Tier 4 不算**：这也是正确的。Tier 2/3 的 `estimate_uncompacted_tokens` 估算是为了决定"要不要压缩"——需要算全量（含 tool_result）才知道真实上下文大小。Tier 4 的估算是为了 token budget 截断——序列化后的 dict 里已经没有 tool_result（spec 13 把 tool_result 折叠为文本占位或裁剪 marker）。

### 问题

差异是合理的，但实现是分散的：
- Tier 0 的 `estimate_messages_tokens` 和 Tier 4 的 `_estimate_chat_message_tokens` 逻辑几乎相同（都是 content + tool_calls + 4/msg），只是 Tier 0 多了 reasoning_content。没有共享函数。
- 新人看到三个估算函数，不知道该用哪个、为什么有差异。

## Goals / Non-Goals

**Goals:**

- 提取 `estimate_dict_message_tokens(msg: dict, include_reasoning: bool = False)` 共享函数，统一 Tier 0 和 Tier 4 的 dict-format 估算。
- 在 `transcript_renderer.py` 顶部添加文档化注释，说明三个估算函数的关系和使用场景。
- 删除 Tier 4 的 `_estimate_chat_message_tokens` 私有函数，改为调用共享函数。

**Non-Goals:**

- 不改变任何估算函数的返回值（保持向后兼容）。
- 不引入真实 tokenizer（tiktoken 等）。
- 不合并 Message-format 和 dict-format 的估算（它们服务不同的数据结构，合并不自然）。
- 不改 `estimate_tokens` 基础函数（`model_registry.py`）。
- 不动 Tier 0 的 `estimate_messages_tokens` 外部接口（被 `agent_runner.py` 导入使用）。

## Decisions

### D1. estimate_dict_message_tokens 的 include_reasoning 参数

- **选择**：`estimate_dict_message_tokens(msg: dict, include_reasoning: bool = False) -> int`。默认 `include_reasoning=False`（Tier 4 场景）。Tier 0 调用时传 `include_reasoning=True`。
- **理由**：
  - Tier 0 的 in-memory messages 有 reasoning_content，需要算。
  - Tier 4 的序列化 dict 没有 reasoning_content（spec 13 不回传），不需要算。
  - 用参数而非两个函数，避免代码重复。
- **替代方案**：两个函数 `estimate_dict_message_tokens` 和 `estimate_dict_message_tokens_with_reasoning`。被否决——代码重复。

### D2. 共享函数放在 transcript_renderer.py

- **选择**：`estimate_dict_message_tokens` 放在 `transcript_renderer.py`（change-1 新建的模块）。
- **理由**：
  - `transcript_renderer.py` 已经有 `estimate_full_message_tokens(list[Message])`（Message-format 估算），把 dict-format 估算也放这里，形成"token 估算公共模块"。
  - 不放 `compact_pipeline.py`——它是 Tier 0 专用模块，不应被 Tier 4 依赖（避免循环依赖：Tier 4 已导入 `compact_pipeline` 的策略表，再加估算函数会让依赖更深）。
  - 不放 `model_registry.py`——它是基础工具模块，不应包含 message-level 估算逻辑。
- **替代方案**：新建 `token_estimator.py` 模块。被否决——模块太碎，`transcript_renderer.py` 已经是 token + transcript 的公共模块。

### D3. 文档化注释格式

- **选择**：在 `transcript_renderer.py` 顶部添加块注释：
  ```python
  """Token estimation for the four-layer compaction system.

  Three message-level estimators, each serving a different input format:
  
  1. estimate_dict_message_tokens(msg: dict) — OpenAI chat message dict format.
     Used by Tier 0 (in-memory ReAct loop) and Tier 4 (cross-run serialized history).
     Counts: content + tool_calls.function.name/arguments + 4/msg overhead.
     Tier 0 passes include_reasoning=True (in-memory messages have reasoning_content).
     Tier 4 passes include_reasoning=False (spec 13: thinking not replayed cross-run).
  
  2. estimate_full_message_tokens(messages: list[Message]) — DB Message format.
     Used by Session Memory (should_extract) and Tier 2/3 (estimate_uncompacted_tokens).
     Counts: text + thinking + tool_use args + tool_result + effective_prompt.
     These layers need the FULL picture (including tool_results) to decide compaction.
  
  3. estimate_tokens(text: str) — base function in model_registry.py.
     4 chars ≈ 1 token. Used by all three above for per-field estimation.
  """
  ```
- **理由**：新人一看模块顶部就知道三个函数的关系，不用追代码。

### D4. Tier 0 的 estimate_messages_tokens 内部重构

- **选择**：`compact_pipeline.estimate_messages_tokens` 改为：
  ```python
  def estimate_messages_tokens(messages: list[dict]) -> int:
      from app.services.transcript_renderer import estimate_dict_message_tokens
      return sum(estimate_dict_message_tokens(m, include_reasoning=True) for m in messages)
  ```
  外部接口和返回值不变。
- **理由**：消除 Tier 0 和 Tier 4 的 dict-format 估算代码重复。
- **风险**：引入 `compact_pipeline` → `transcript_renderer` 的导入。检查循环依赖：`transcript_renderer` 导入 `compact_pipeline.summarize_tool_result`，`compact_pipeline` 导入 `transcript_renderer.estimate_dict_message_tokens` → 循环依赖！
- **缓解**：用延迟导入（`from app.services.transcript_renderer import estimate_dict_message_tokens` 在函数内部），打破循环。或者把 `estimate_dict_message_tokens` 放在一个不导入 `compact_pipeline` 的更低层模块。实际上 `estimate_dict_message_tokens` 不需要导入 `compact_pipeline`（它只算 dict 的 content + tool_calls），所以放在 `transcript_renderer.py` 不会循环——`transcript_renderer` 导入 `compact_pipeline.summarize_tool_result`，但 `estimate_dict_message_tokens` 本身不依赖 `compact_pipeline`。`compact_pipeline` 在 `estimate_messages_tokens` 函数内部延迟导入 `transcript_renderer.estimate_dict_message_tokens`，打破循环。

## Risks / Trade-offs

- **[循环依赖]** → `compact_pipeline` 导入 `transcript_renderer`，`transcript_renderer` 导入 `compact_pipeline.summarize_tool_result`。用延迟导入（函数内部 import）打破循环。或者把 `estimate_dict_message_tokens` 放在 `model_registry.py`（基础模块），但那里不应该有 message-level 逻辑。最终选择延迟导入。

- **[估算结果微变]** → 如果 `estimate_dict_message_tokens` 的实现与 Tier 0 原来的 `estimate_messages_tokens` 有细微差异（如 reasoning_content 的处理），可能导致 Tier 0 的 ratio 计算微变。缓解：单元测试验证改造前后结果一致。

- **[本变更价值较低]** → 如果 change-1 和 change-2 已经解决了主要问题，本变更只是"锦上添花"的代码整理。可以低优先级推进，甚至合并到 change-1 或 change-2 中。但保持独立 change 的好处是：回滚时不影响功能修复。

## Migration Plan

1. 在 `transcript_renderer.py` 中新增 `estimate_dict_message_tokens`，添加文档化注释。
2. 修改 `compact_pipeline.estimate_messages_tokens` 内部调用 `estimate_dict_message_tokens`（延迟导入）。
3. 修改 `conversation_context.py`，删除 `_estimate_chat_message_tokens`，改为调用 `estimate_dict_message_tokens`。
4. 测试：验证 Tier 0 / Tier 4 估算结果与改造前一致。

## Open Questions

1. **是否值得独立 change？** 如果 change-1 已经在做 `estimate_full_message_tokens`，可以把 `estimate_dict_message_tokens` 也放进 change-1。但 change-1 的范围已经不小（transcript renderer + Session Memory + Tier 2/3），加 dict-format 统一会让它更大。保持独立 change 更清晰。也可以在实现时合并到 change-1——由实现者决定。
