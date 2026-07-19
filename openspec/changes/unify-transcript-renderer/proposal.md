## Why

Session Memory（`session_memory.py`）和 Tier 2/3（`context_compaction_service.py:compact_conversation`）的 LLM 摘要器都存在 **Transcript Blindness（transcript 盲视）**：两者的 `_render_transcript()` / `_message_text()` 只提取 `type == "text"` 的 part，完全丢弃 `tool_use` 和 `tool_result`。一个"分析项目"任务里 agent 调了 `fs_list(depth=3)` → `fs_read` × 5 → `code_explore` → 写出分析报告，摘要器只看到最后的分析报告文本，看不到探索过程。下一轮 agent 拿到的摘要里没有文件结构、没有代码发现，于是重新探索 → 幻觉。

此外 Session Memory 的 token 估算 `sum(estimate_tokens(_message_text(m)))` 也只算 text part，**严重低估**实际上下文大小（一个 7 工具 turn 的 text 可能只有 500 token，但 tool_result 有 50k token），导致触发阈值失准。

两个摘要 prompt 也只要求保留"用户核心目标、关键决策、产物、待跟进"，缺失了结构化维度（已探索的文件/目录结构、关键代码发现、执行过的命令及结果、架构理解）。

## What Changes

- **新建共享模块 `transcript_renderer.py`**：提供 `render_tool_aware_transcript()` 和 `estimate_full_message_tokens()` 两个公共函数，供 Session Memory 和 Tier 2/3 复用。tool_result 按工具类型用 Tier 0 已验证的 `ToolResultSummarizer` 策略表提取关键字段（而非全量灌入），在 token 省、信息密度高之间取平衡。
- **Session Memory 接入**：`session_memory.py` 的 `_render_transcript` / `_message_text` 替换为调用共享模块；`should_extract` 的 token 估算改用 `estimate_full_message_tokens`；summary prompt 增加结构化维度。
- **Tier 2/3 接入**：`context_compaction_service.py` 的 `_render_transcript` / `_message_text` 替换为调用共享模块；`_summarise` 的 prompt 增加结构化维度；`estimate_uncompacted_tokens` 已用 `_message_token_estimate`（含 tool_result），保持不变。
- **删除重复代码**：`session_memory.py` 和 `context_compaction_service.py` 各自的 `_render_transcript` / `_message_text` 移除，统一委托给共享模块。

## Capabilities

### New Capabilities

- `transcript-rendering`: 将 DB Message 列表渲染为包含工具调用信息的 plain-text transcript 的公共能力，供 LLM 摘要器（Session Memory / Tier 2/3）消费。定义 tool_result 按 tool 类型差异化保留策略、token 估算含全量 part、transcript 格式契约。

### Modified Capabilities

- `conversation-context`: Session Memory 和 Tier 2/3 的 transcript 渲染从 text-only 升级为 tool-aware；summary prompt 增加结构化保留维度（文件结构、代码发现、命令结果、架构理解）。`compact_conversation` 和 `SessionMemory.extract` 的外部接口签名不变，仅内部实现替换。

## Impact

- **后端**：
  - **新增** `backend/app/services/transcript_renderer.py` — `render_tool_aware_transcript()` + `estimate_full_message_tokens()` + per-tool transcript 策略。
  - `backend/app/memory/session_memory.py` — 删除 `_render_transcript` / `_message_text`，改为导入共享模块；`should_extract` 的 token 估算改用 `estimate_full_message_tokens`；`extract` 的 system prompt 增加结构化维度。
  - `backend/app/services/context_compaction_service.py` — 删除 `_render_transcript` / `_message_text`，改为导入共享模块；`_summarise` 的 prompt 增加结构化维度。
- **复用**：`backend/app/services/compact_pipeline.py:summarize_tool_result` 已存在且已测试，transcript renderer 复用其策略表（stage=1 输出格式适合 transcript 消费）。
- **DB / API / 事件**：无变更。不改 `ContextSummary` 表结构，不改 `compact_conversation` / `SessionMemory.extract` 的外部接口。
- **依赖**：无新第三方依赖。`transcript_renderer.py` 复用 `compact_pipeline.summarize_tool_result`（纯 Python 正则 + JSON 解析）。
- **向后兼容**：
  - `compact_conversation` 和 `SessionMemory.extract` 的调用方无感知（签名不变）。
  - 摘要质量提升但格式不变（仍是纯文本 summary 字符串）。
  - Session Memory 的 `covers_up_to` 语义不变，但 token 估算修正后触发频率可能变化（更准确，可能略晚触发——因为真实 token 数比 text-only 估算大，但 `MINIMUM_TOKENS_TO_INIT=10000` 的绝对值不变）。
- **测试**：
  - transcript renderer 单元测试（含工具调用的消息渲染、各 tool 类型的保留策略）。
  - Session Memory token 估算修正测试（含 tool_result 的消息估算 >> 只算 text）。
  - Tier 2/3 transcript 质量回归测试（摘要中包含文件结构信息）。
