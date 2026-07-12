## Why

AChat 当前有三层上下文压缩机制：

1. **Tier 1 结构裁剪**（`_mid_run_compact`）：ReAct loop 内 `total_tokens > 90% × model_limit` 时，裁剪旧 tool_result + 折叠旧消息。纯内存操作，不调 LLM。
2. **Tier 2 LLM 静默摘要**（`_maybe_auto_compact_hook`）：turn 结束后 `watermark ≥ 10` 或 `token > 87%` 时触发，调 LLM 对全量未压缩消息生成摘要，存为 `ContextSummary`。
3. **Tier 3 手动触发**（`/compact`）：用户主动调用，同 Tier 2 逻辑但广播系统消息。

其中 Tier 2/3 的 `compact_conversation()` 在触发时一次性调 LLM，输入是**全量未压缩消息**——对话越长，这次调用的输入 token 越大（可能 50K-100K+），成本高、延迟明显。

Claude Code 的 Session Memory 模式可以改善这个问题：在对话进行中以**增量方式**异步提取会话摘要，每次只处理最近的增量（~5000 token）。Tier 2/3 触发时优先复用已存的 Session Memory，避免对全量历史做一次性大摘要。

需要澄清：Session Memory 的增量提取本身**也调 LLM**，但每次输入小（增量 + 旧摘要），远小于 Tier 2 一次性处理全量历史的成本。收益在于**把一次昂贵的大调用拆成多次便宜的小调用**，且 Tier 2/3 触发时可能完全跳过 LLM 调用（Session Memory 已覆盖时）。

当前 `context_compaction_service.py` 另有两个缺口：
- Compaction 裁切消息时不处理 `tool_use / tool_result` 链断裂，可能产生孤立 tool_result 导致 API 报错
- Compaction 后模型丢失已注册能力的上下文（工具列表、附件、dispatch plan），没有能力复灌

## What Changes

**A. 新增 SessionMemory 层**

- 新增 `backend/app/memory/session_memory.py`，负责增量提取和维护会话摘要
- 触发条件：会话 token 达到阈值（`minimum_message_tokens_to_init = 10000`）后，每增量 `5000` token 或每 `3` 次工具调用触发一次
- 提取方式：`asyncio.create_task` 异步 background task，调 `_generate_fn` 对增量消息 + 已有摘要生成新摘要（增量拼接，覆盖更新）
- **不是 fork subagent**——只是一次普通 LLM chat completion 调用，不需要 agent loop / 工具白名单 / 沙箱（PG 架构由 DB 层管权限）

**B. Compaction 三路复用 Session Memory**

`compact_conversation()` 修改为三路分支，按 Session Memory 的覆盖情况选择最优路径：

- **完全覆盖**：Session Memory 的覆盖范围 ≥ 待压缩区间 → 直接用，零 LLM 调用
- **部分覆盖**：Session Memory 覆盖了待压缩区间的一部分，有缺口 → 缺口消息 + 旧摘要一起调 LLM（小输入）
- **无 Session Memory**：对话太短未触发、或 `_generate_fn` 不可用 → 回退原路径（全量消息调 LLM），行为完全不变

**C. 与 Tier 1 的关系：完全不冲突**

Tier 1（`_mid_run_compact`）是 ReAct loop 内的纯内存操作——裁 tool_result、折叠旧消息。它不读 DB、不读 ContextSummary、不读 Session Memory。Session Memory 的增量提取在 turn 结束后的 background task 中执行，两者在时间轴上错开，互不干扰。

**D. 断点保护**

- `compact_conversation` 的消息裁切逻辑增加 `tool_use / tool_result` 链检测
- 裁切位置落在 tool 链中间时，强制向头部平移到链的起点
- 确保不产出孤立的 `tool_result` 消息

**E. 能力复灌**

- Compaction 完成后，重建当前会话的活跃能力上下文（工具列表、附件列表、dispatch plan 状态）
- 确保模型"醒来"后虽然历史细节没了，但技能蓝图完整

## Capabilities

### New Capabilities

- `session-memory`: 会话级增量摘要层——对话进行中异步提取摘要，Tier 2/3 Compaction 时按覆盖情况复用

### Modified Capabilities

- `conversation-compaction`: Compaction 三路复用 Session Memory；增加 tool 链断点保护；增加能力复灌

## Impact

- **后端代码**：新增 `backend/app/memory/session_memory.py`；修改 `context_compaction_service.py`（三路复用 + 断点保护 + 能力复灌）、`memory_service.py`（触发 hook）
- **数据库**：`context_summaries` 表新增 `summary_type` 列（`VARCHAR(16)`, default `'compaction'`），区分 `'session'` / `'compaction'`；存量行自动 `'compaction'`
- **API**：无外部接口变更
- **前端**：无直接影响
- **风险**：Session Memory 增量提取是异步 background task，需确保不阻塞主对话流；增量摘要的拼接质量需验证；覆盖缺口场景下仍需一次小 LLM 调用
