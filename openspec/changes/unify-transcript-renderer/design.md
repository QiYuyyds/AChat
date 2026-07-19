## Context

AChat 的四层压缩系统中，Session Memory（`session_memory.py`）和 Tier 2/3（`context_compaction_service.py:compact_conversation`）是两个 LLM-backed 摘要层。它们都通过 `_render_transcript()` 把 DB Message 列表渲染为纯文本 transcript，再交给 LLM 生成摘要。

### 现状的三个核心缺陷

1. **Transcript Blindness**：两者的 `_message_text()` 都只提取 `type == "text"` 的 part：
   ```python
   # session_memory.py:304 和 context_compaction_service.py:573 — 完全一样的逻辑
   def _message_text(msg: Message) -> str:
       texts = [p.get("content", "") for p in msg.parts_list
                if p.get("type") == "text" and p.get("content")]
       return "\n".join(texts).strip()
   ```
   一个"分析项目"任务里 agent 调了 `fs_list(depth=3)` → `fs_read` × 5 → `code_explore` → 写出分析报告。摘要器只看到最后的分析报告文本，完全看不到探索过程。下一轮 agent 拿到的摘要里没有文件结构、没有代码发现。

2. **Token 估算失准（Session Memory 独有）**：`should_extract` 用 `sum(estimate_tokens(_message_text(m)))` 只算 text part 的 token。一个 7 工具 turn 的 text 可能只有 500 token，但 tool_result 有 50k token。估算严重偏低导致触发时机不准。

3. **Summary prompt 缺失结构化维度**：两个摘要 prompt 都只要求保留"用户核心目标、关键决策、产物、待跟进"。缺失了：已探索的文件/目录结构、关键代码发现、执行过的命令及结果、架构理解。

### 与 Tier 0 的关系

Tier 0（`compact_pipeline.py`）已在 `fix-tier0-run-internal-compaction` change 中落地了 `ToolResultSummarizer` 策略表——按 tool name + mode 差异化保留语义骨架。该策略表已经过单元测试和端到端验证。本变更复用该策略表，将其输出从「marker 替换」改为「transcript 行渲染」。

### 重复代码现状

`session_memory.py` 和 `context_compaction_service.py` 各有一份完全相同的 `_render_transcript` + `_message_text`，已经是「复制漂移」状态。本变更新建共享模块消除重复。

## Goals / Non-Goals

**Goals:**

- 新建 `transcript_renderer.py`，提供 `render_tool_aware_transcript()` 和 `estimate_full_message_tokens()`，供 Session Memory 和 Tier 2/3 复用。
- transcript 中 tool_result 按 tool 类型用 Tier 0 的 `ToolResultSummarizer` 策略表（stage=1）提取关键字段，不全量灌入。
- Session Memory 的 `should_extract` token 估算改用含全量 part 的 `estimate_full_message_tokens`。
- 两个摘要 prompt 增加结构化保留维度：文件结构、代码发现、命令结果、架构理解。
- 消除 `session_memory.py` 和 `context_compaction_service.py` 的重复 `_render_transcript` / `_message_text`。

**Non-Goals:**

- 不动 Tier 0（`compact_pipeline.py` / `compact_markers.py`）——已落地。
- 不动 Tier 4（`conversation_context.py`）——留给 `fix-tier4-cross-run-compaction` change。
- 不改 `compact_conversation` / `SessionMemory.extract` 的外部接口签名。
- 不改 DB schema（`ContextSummary` 表不变）。
- 不引入 LLM-backed 文件摘要（transcript 中的 tool_result 用正则提取，不调 LLM）。
- 不调 Session Memory 的阈值常量（`MINIMUM_TOKENS_TO_INIT = 10_000` 等）——估算修正后触发频率变化留给生产观察。

## Decisions

### D1. 复用 Tier 0 的 ToolResultSummarizer 策略表

- **选择**：`transcript_renderer.py` 导入 `compact_pipeline.summarize_tool_result`，对每个 `tool_result` part 调用 `summarize_tool_result(tool_name, args, content, stage=1)` 获取压缩后的内容，再格式化为 transcript 行。
- **理由**：
  - Tier 0 的策略表已经过单元测试（`test_compact_pipeline.py`）和端到端验证。
  - stage=1 是"轻剪"策略，保留最多语义（fs_list 保 name+relativePath、fs_read full 转 outline、bash 保末 20 行、code_explore 完整保留），适合作为摘要器的输入——既要信息丰富又不能太长。
  - 复用避免另写一套策略表。
- **替代方案**：全量灌入 tool_result 到 transcript。被否决——一个 7 工具 turn 的 tool_result 可能 50k token，transcript 会爆炸。LLM 摘要成本高且注意力分散。

### D2. transcript 行格式

- **选择**：每个 agent message 渲染为多行，tool_use 和 tool_result 紧跟在 text 之后：
  ```
  Agent：[text content if any]
    ↳ tool_use: fs_list(path='src', depth=3)
    ↳ tool_result: [fs_list] src/ 含 5 文件、3 子目录 | {压缩后的 JSON 内容}
    ↳ tool_use: fs_read(path='src/index.ts', mode='full')
    ↳ tool_result: [fs_read] outline: 12 条签名 | {压缩后的 JSON 内容}
  ```
  user / system message 保持原来的 `用户：text` / `系统：text` 格式。
- **理由**：
  - `↳` 前缀让 LLM 能区分"agent 说了什么"和"agent 做了什么"。
  - tool_result 中先放 summary 行（从 `summarize_tool_result` 的返回值提取），再放压缩后的完整内容，让 LLM 快速 scan。
  - `code_explore` 完整保留（Tier 0 策略表已保证），不做二次压缩。

### D3. estimate_full_message_tokens 复用 context_compaction_service 的 _message_token_estimate

- **选择**：`transcript_renderer.py` 的 `estimate_full_message_tokens()` 直接复用 `context_compaction_service._message_token_estimate` 的逻辑（已含 text + thinking + tool_use args + tool_result），提取为公共函数。
- **理由**：
  - `context_compaction_service._message_token_estimate` 已经正确计算了全量 part 的 token（line 583-598），只是 Session Memory 没有用它。
  - 提取为公共函数后，Session Memory 的 `should_extract` 调用它即可获得准确的 token 估算。
  - `context_compaction_service.estimate_uncompacted_tokens` 也改为调用它（已经是了，只是从私有函数改为公共模块的函数）。

### D4. summary prompt 结构化维度增强

- **选择**：两个摘要 prompt（Session Memory 的 `extract` 和 Tier 2/3 的 `_summarise`）都增加以下保留维度：
  ```
  - 已探索的文件/目录结构（路径 + 关键发现）
  - 执行过的关键命令及其结果摘要
  - 架构理解与代码结构发现
  ```
  放在现有的"用户核心目标、关键决策、产物、待跟进"之后。
- **理由**：
  - transcript 已经包含工具信息了，但 prompt 不引导 LLM 保留的话，LLM 可能还是只摘要 text 内容。
  - 新增维度针对"分析项目"类任务——这类任务的探索过程（文件结构、代码发现）是跨 run 记忆的核心。
- **替代方案**：不改 prompt，让 LLM 自己判断保留什么。被否决——LLM 默认倾向摘要"对话内容"而非"工具发现"，需要显式引导。

### D5. 共享模块位置：backend/app/services/transcript_renderer.py

- **选择**：放在 `services/` 下，与 `compact_pipeline.py` / `context_compaction_service.py` 同级。
- **理由**：
  - 它是 L3 服务层工具，被 `session_memory.py`（L3 `memory/` 下）和 `context_compaction_service.py`（L3 `services/` 下）共用。
  - 放 `services/` 与 `compact_pipeline.py` 同级，导入路径短。
  - 不放 `memory/` 下——`context_compaction_service.py` 不在 `memory/` 下，放 `memory/` 会导致跨目录导入。

## Risks / Trade-offs

- **[transcript 变长导致 LLM 摘要成本上升]** → 缓解：`summarize_tool_result(stage=1)` 已经把 tool_result 压缩到 ~10-20% 原始大小。一个 7 工具 turn 的 transcript 约 2-5k token（vs 全量 50k），LLM 摘要成本可控。

- **[Session Memory 触发频率变化]** → 估算修正后真实 token 数变大，`MINIMUM_TOKENS_TO_INIT=10000` 的绝对阈值不变，所以触发可能略晚（之前因为低估，10k text-only 实际可能是 30k 全量）。这是修正而非退化。生产中观察后可调阈值。

- **[摘要 prompt 增强导致摘要变长]** → 缓解：`max_tokens=1024`（Tier 2/3）和 Session Memory 的 `generate_fn` 限制不变。新增维度是引导 LLM 关注，不是要求输出更长。

- **[tool_use 的 args 如何提取]** → DB 里 `tool_use` part 的 `args` 字段是 dict（`persist_event` 写入）。transcript renderer 直接 `json.dumps(args, ensure_ascii=False)` 即可。如果 args 里有敏感信息（API key 等），已有 `fs_service` 的路径沙箱保证不会泄露 workspace 外的文件。

## Migration Plan

无 DB 迁移、无 API 变更、无事件契约变更。纯后端 Python 实现：

1. **新增模块**：`transcript_renderer.py`，先写好但不接入。
2. **Session Memory 接入**：删除 `_render_transcript` / `_message_text`，改为导入共享模块；`should_extract` token 估算改用 `estimate_full_message_tokens`；prompt 增强。
3. **Tier 2/3 接入**：删除 `_render_transcript` / `_message_text`，改为导入共享模块；`_message_token_estimate` 提取为公共函数（或直接调用共享模块的 `estimate_full_message_tokens`）；prompt 增强。
4. **测试覆盖**：transcript renderer 单元测试、Session Memory token 估算修正测试、Tier 2/3 摘要质量回归。
5. **回滚策略**：共享模块是纯新增 + 替换调用点，回滚只需还原 `session_memory.py` 和 `context_compaction_service.py` 的 import 即可。

## Open Questions

1. **stage=1 的输出格式是否适合 transcript 消费？** stage=1 返回的是 JSON 字符串（如 `{"name": ..., "relativePath": ...}`），放在 transcript 里 LLM 能理解吗？→ 可以。LLM 对 JSON-in-text 的理解能力足够。如果生产中发现 LLM 摘要质量下降，可考虑在 transcript renderer 里把 JSON 转为更可读的缩进格式。

2. **Session Memory 的 `covers_up_to` 与修正后 token 估算的协同？** 估算修正后 `should_extract` 会更晚触发（因为之前低估）。但 `extract` 的 transcript 也变长了（含工具信息），单次 LLM 调用的输入更大。需要观察是否需要上调 `MINIMUM_TOKENS_BETWEEN_UPDATE`。留给生产观察。
