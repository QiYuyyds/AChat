# Design — add-session-memory-layer

## 背景与定位

### 现有三层上下文压缩

AChat 当前已有三层压缩机制，本变更不替代任何一层，而是在 Tier 2/3 的内部优化 LLM 调用成本：

| Tier | 触发 | 操作 | LLM | 作用域 | 本变更 |
|------|------|------|-----|--------|--------|
| **Tier 1** `_mid_run_compact` | ReAct loop 内 `token > 90%` | 裁剪旧 tool_result + 折叠旧消息 | ❌ | 内存 messages 列表 | **不修改** |
| **Tier 2** `_maybe_auto_compact_hook` | turn 结束后 `watermark≥10` 或 `token>87%` | `compact_conversation(silent=True)` | ✅ 全量 | DB (Message + ContextSummary) | **修改内部逻辑** |
| **Tier 3** 手动 `/compact` | 用户主动 | `compact_conversation(silent=False)` | ✅ 全量 | DB | **修改内部逻辑** |

Tier 1 是纯内存操作，在 ReAct loop 内对 dict 列表做结构裁剪，不读 DB、不读 ContextSummary、不读 Session Memory。Session Memory 的增量提取在 turn 结束后的 background task 中执行——两者在时间轴上错开，完全不冲突：

```
turn 开始 → [Tier 1 检查] → LLM → tool → [Tier 1 检查] → ... → turn 结束
                                                              ↓
                                           [Session Memory 增量提取 (background)]
                                           [Tier 2 auto-compact 检查 (background)]
```

### 缺失的中间层

Tier 2/3 的 `compact_conversation()` 在触发时一次性调 LLM，输入是**全量未压缩消息**——对话越长，这次调用的输入 token 越大（可能 50K-100K+），成本高、延迟明显。

中间缺一层：**会话进行中持续维护的摘要层**。有了这层后，Tier 2/3 触发时可以优先复用已存的摘要，避免对全量历史做一次性大摘要。

### 与 Claude Code 的差异

Claude Code 用 fork subagent 提取 Session Memory（因为记忆是文件，需要 agent loop + 工具白名单 + `FileEditTool`）。AChat 的记忆在 PG 表里，Session Memory 提取是纯粹的"输入文本 → LLM 生成摘要 → 写库"流程，用 `asyncio.create_task` 包一次 `_generate_fn` 调用即可，不需要 fork subagent。

## 决策

### D1. Session Memory 存储：复用 ContextSummary 表 + summary_type 区分

**选择**：在 `context_summaries` 表增加 `summary_type` 列（`compaction` / `session`），Session Memory 记 `summary_type='session'`。
**替代**：新建 `session_memories` 表。
**理由**：两者都是"对话级摘要"，结构一致（conversation_id + summary_text + token 覆盖范围）。用 `summary_type` 区分避免双表查询。每个 conversation 同时只有一条 `summary_type='session'` 的记录（增量更新，而非追加）。

### D2. 触发条件：token 阈值 + 工具调用次数双重触发

```python
MINIMUM_TOKENS_TO_INIT = 10000       # 会话启动阈值
MINIMUM_TOKENS_BETWEEN_UPDATE = 5000 # 增量 token 阈值
TOOL_CALLS_BETWEEN_UPDATES = 3       # 工具调用次数阈值
```

- 会话未到 10K token：不启用 Session Memory
- 达到 10K 后：每 5000 token 增量 或 每 3 次工具调用（满足任一）触发一次更新
- 在 tool_use 链中间（最后一轮是 assistant tool_use 且未收到 tool_result）时不触发——寻找自然断点

**选择**：沿用 Claude Code 的阈值。
**理由**：这些阈值在 Claude Code 中经过验证，适合一般对话节奏。后续可通过 Settings 配置化。

### D3. 提取方式：asyncio.create_task + 增量拼接（非 fork subagent）

```
对话进行中 (turn 结束后)
  → should_extract_session_memory() == true
  → asyncio.create_task(_safe_extract_session_memory())
    → 读取最近未摘要的消息（从上次覆盖点到当前）
    → LLM 调用：将增量消息 + 已有摘要 → 新摘要
    → 更新 session_memories 记录（覆盖）
```

这是普通异步 task，不是 fork subagent。Claude Code 用 fork subagent 是因为记忆是文件、需要 agent loop 调 `FileEditTool` 操作。AChat 的 Session Memory 是一次 LLM chat completion 调用 + 一次 PG UPDATE，不需要 agent loop / 工具白名单 / 沙箱。

LLM prompt：
```
你是会话摘要助手。以下是当前会话的已有摘要和新增对话内容。
请将新增内容整合进已有摘要，保持摘要简洁但信息完整。
已有摘要：{existing_summary}
新增内容：{recent_messages}
输出：更新后的摘要
```

**选择**：增量拼接（已有摘要 + 增量 → 新摘要），而非每次从零重新摘要。
**理由**：增量拼接成本低（输入只有增量 + 旧摘要），且保真度逐步积累。从零重摘要会丢失早期细节。

### D4. Compaction 三路复用 Session Memory（覆盖缺口处理）

Session Memory 是增量维护的——每次只覆盖最近 ~5000 token 的消息。但 Tier 2/3 的 `compact_conversation` 需要压缩**所有未压缩的消息**（从上次 ContextSummary 覆盖点到当前）。Session Memory 的覆盖范围可能不完全对齐待压缩区间，存在覆盖缺口：

```
时间轴:
  msg1...msg5       msg6...msg15          msg16...msg20    msg21...msg25
  ←上次Compaction→   ←Session Memory覆盖→   ←缺口(未覆盖)→   ←recent 6(保留)→
  ContextSummary #1    summary_type='session'                  KEEP_RECENT
```

`compact_conversation` 修改为三路分支：

```python
async def compact_conversation(conversation_id, *, silent=False):
    # ... 加载待压缩消息 to_compact ...

    session_mem = await get_session_memory(conversation_id)

    if session_mem and session_mem.covers_up_to >= to_compact[-1].created_at:
        # 情况 1: Session Memory 完全覆盖了待压缩区间
        # → 直接用, 零 LLM 调用
        summary_text = session_mem.summary

    elif session_mem:
        # 情况 2: 部分覆盖, 有缺口
        # → 缺口消息 + Session Memory 摘要 一起调 LLM (小输入)
        gap_messages = [m for m in to_compact
                        if m.created_at > session_mem.covers_up_to]
        gap_transcript = _render_transcript(gap_messages, agent_names)
        summary_text = await _summarise(
            gap_transcript, session_mem.summary,
            model_provider, model_id, api_key, api_base_url,
            parent_system_prompt=parent_system_prompt,
        )

    else:
        # 情况 3: 无 Session Memory (对话太短未触发, 或 _generate_fn 不可用)
        # → 回退原路径, 行为完全不变
        summary_text = await _summarise(
            transcript, prior_summary,
            model_provider, model_id, api_key, api_base_url,
            parent_system_prompt=parent_system_prompt,
        )

    # 后续不变: 持久化 ContextSummary + 裁切 + 断点保护(D5) + 能力复灌(D6)
```

三路分支的成本对比：

| 情况 | LLM 调用 | 输入大小 | vs 现状 |
|------|---------|---------|--------|
| 完全覆盖 | ❌ 零 | — | 省一次大调用 |
| 部分覆盖 | ✅ 一次 | 缺口消息 + 旧摘要（小） | 输入比全量小得多 |
| 无 Session Memory | ✅ 一次 | 全量未压缩消息 | 与现状完全一致 |

**三种情况都比现状更好或相等**——不会比现状更差。

**整体 LLM 成本对比**（30 轮对话，Tier 2 触发 3 次）：

```
现状:
  3 次大 LLM 调用 (每次全量未压缩消息做输入, 30K-50K tok/次)
  总输入 ≈ 120K tok

有 Session Memory 后:
  5 次小 LLM 调用 (增量提取, ~5K tok/次)
  + 0-1 次小 LLM 调用 (缺口补摘要, ~2K tok)
  总输入 ≈ 27-32K tok  (省 ~73%)
```

**选择**：三路分支而非简单的"有就跳过、无就原路径"。
**理由**：部分覆盖是常态——Session Memory 增量提取和 Tier 2 触发频率不同步，缺口几乎必然存在。如果只做"全有或全无"的二选一，大部分时候会走回退路径，收益被吃掉。三路分支确保即使有缺口也能利用已积累的摘要。

### D5. 断点保护：tool_use / tool_result 链不裁断

当前 `compact_conversation` 的消息选择逻辑：保留最近 `KEEP_RECENT_MESSAGES=6` 条，其余的做摘要。但不检查裁切边界。

修改：
```python
def _find_safe_cut_point(messages: List[Message]) -> int:
    """找到安全的裁切点，不落在 tool_use/tool_result 链中间。"""
    cut = len(messages) - KEEP_RECENT_MESSAGES
    # 向头部扫描：如果 cut 位置是 tool_result（孤立），向前找到对应的 tool_use
    while cut > 0 and _is_orphan_tool_result(messages, cut):
        cut -= 1
    # 向头部扫描：如果 cut 位置是 tool_use（等待 result），向前跳过
    while cut > 0 and _is_pending_tool_use(messages, cut):
        cut -= 1
    return cut
```

- `_is_orphan_tool_result`：cut 位置的消息是 tool_result，但 cut-1 不是对应的 tool_use
- `_is_pending_tool_use`：cut 位置的消息是 tool_use，但 cut+1（在保留区）才是 tool_result

### D6. 能力复灌

Compaction 完成后，在 Summary 消息之后注入一段"能力上下文重建"信息：

```
[能力上下文]
- 当前可用工具: {tool_names}
- 活跃附件: {attachment_list}
- 进行中的派发计划: {dispatch_plan_summary} (如有)
```

**选择**：以 system-reminder 形式注入到 Summary 后面。
**理由**：模型压缩历史后丢失了已注册能力的上下文。这段注入确保模型知道"我还能用什么工具"。

### D7. Session Memory 的生命周期

- 会话开始：无 Session Memory
- 达到阈值（10K token）：首次提取，创建记录
- 后续：增量更新，覆盖同一条记录；每次更新推进 `covers_up_to` 时间戳
- 会话结束：保留记录（跨 run 恢复时可用）
- 新会话：不继承旧会话的 Session Memory
- `covers_up_to` 字段：记录 Session Memory 摘要覆盖到的最后一条消息的 `created_at`，用于 D4 的覆盖缺口判断

### D8. 与现有三层压缩 + STM / LTM 的关系

```
┌─────────────────────────────────────────────────────────────┐
│                    完整上下文管理全景                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ReAct loop 内 (run 进行中)                                 │
│  ┌──────────────────────────────────────────────────┐       │
│  │ Tier 1: _mid_run_compact  (纯内存, 不调 LLM)      │       │
│  │ 触发: token > 90% × model_limit                  │       │
│  │ 操作: 裁 tool_result + 折叠旧消息                 │       │
│  │ 与 Session Memory 关系: 无 (时间轴错开, 不读 DB)   │       │
│  └──────────────────────────────────────────────────┘       │
│                                                             │
│  turn 结束后 (background task)                              │
│  ┌──────────────────────────────────────────────────┐       │
│  │ Session Memory 增量提取 (asyncio.create_task)     │       │
│  │ 触发: token≥10K 后, 每+5K tok 或 3 次 tool call  │       │
│  │ 操作: 增量消息+旧摘要 → LLM → 新摘要 (覆盖)       │       │
│  │ LLM: ✅ 小输入 (~5K tok)                         │       │
│  └──────────────────────────────────────────────────┘       │
│  ┌──────────────────────────────────────────────────┐       │
│  │ Tier 2: _maybe_auto_compact_hook (background)     │       │
│  │ 触发: watermark≥10 或 token>87%                  │       │
│  │ 操作: compact_conversation(silent=True)          │       │
│  │   → 三路复用 Session Memory (D4)                  │       │
│  │   → 完全覆盖: 零 LLM                              │       │
│  │   → 部分覆盖: 小 LLM (缺口+旧摘要)                │       │
│  │   → 无覆盖: 回退原路径 (全量 LLM)                 │       │
│  └──────────────────────────────────────────────────┘       │
│                                                             │
│  用户主动                                                   │
│  ┌──────────────────────────────────────────────────┐       │
│  │ Tier 3: /compact (同 Tier 2 逻辑, 广播系统消息)    │       │
│  └──────────────────────────────────────────────────┘       │
│                                                             │
│  记忆层 (跨会话)                                             │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │ STM (内存)     │  │ LTM (PG)       │  │ Preference(PG) │ │
│  │ 最近 N 轮原文   │  │ 跨会话事实      │  │ 用户偏好 KV    │ │
│  │ 不修改          │  │ 不修改          │  │ 不修改          │ │
│  └────────────────┘  └────────────────┘  └────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

Session Memory 不替代 Tier 1（纯内存裁剪）、不替代 STM（原文窗口）、不替代 LTM（跨会话事实）——它填充"会话级渐进摘要"的空白，并优化 Tier 2/3 的 LLM 调用成本。

## 不做

- 不做跨会话的 Session Memory 恢复（新会话从空白开始）
- 不做 Session Memory 的 UI 展示（后续 `add-memory-transparency-ui` 可覆盖）
- 不改 Tier 1（`_mid_run_compact`）——纯内存操作与 Session Memory 无交集
- 不改 STM 的滑动窗口大小
- 不改 LTM 的召回逻辑
- 不做 Session Memory 的 token 精确计算（用 estimate_tokens 估算即可）
- 不用 fork subagent 提取 Session Memory——PG 架构不需要 agent loop / 工具白名单 / 文件沙箱
- 不改 Tier 2/3 的触发条件（`watermark≥10`、`token>87%`、手动 `/compact` 不变）——只改 `compact_conversation` 内部的摘要来源选择
