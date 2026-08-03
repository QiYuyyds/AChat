## Context

### 现状：裁剪无条件执行

`build_history_for`（`conversation_context.py`）中的 `prune_old_tool_results` 和 `fold_old_messages` 在 `_build_history_legacy` 第 533-534 行**无条件调用**——不管 context window 还剩多少空间，只要完整 turn 数 > 2 就裁剪 tool_result，> 4 就折叠旧 turn：

```python
# conversation_context.py L532-534（现状）
merged = prune_old_tool_results(merged)    # 无条件
merged = fold_old_messages(merged)         # 无条件
```

即使模型 context window 是 1M tokens、历史只占了 8%，旧 tool_result 也会被替换成粗糙的 marker。

### 真实场景：短对话即遗忘

```
Run 1: "详细分析一下这个项目"
  → Agent 调用 10 轮工具（fs_list × 1, fs_read × 5, bash × 2, code_explore × 2）
  → ratio ≈ 0.08（远未触发 run 内压缩）
  → run 正常结束

Run 2: "帮我加个测试"
  → build_history_for 加载 10 轮历史
  → prune + fold 无条件执行
  → turn 1~8 被折叠成粗糙的 fold marker
  → LLM 看不到 Run 1 中 fs_read 的具体文件内容
  → 可能需要重新调 fs_read 获取信息 → 浪费一次工具调用
```

### Session Memory 已有但未被历史注入使用

Session Memory 在 Run 1 进行过程中**已经在后台增量提取了摘要**（`SessionMemory.should_extract` → `extract`），但 `build_history_for` **根本不读 Session Memory**——只有 `compact_conversation` 才使用它。而 auto-compact 的触发阈值很高（30 条消息或 87% context window），在上述 10 轮工具调用的场景下不会触发，Session Memory 白做了。

### 0.65 阈值与缓冲带

run 内压缩的 stage 1 触发于 ratio ≥ 0.70。如果一个 run 结束时 ratio = 0.69，run 内压缩没有触发，但下一个 run 一开始全量注入历史，第一轮 model call 前 ratio 就可能超过 0.70，立刻触发 run 内 stage 1 压缩——而 run 内压缩是纯结构化的，没有摘要补偿。

0.65 的意义是留出一个 0.05 的缓冲带：在 run 内压缩不得不触发之前，先在跨 run 注入阶段做一次有 Session Memory 补偿的裁剪。

```
  0.65                    0.70                    0.80    0.88
   │                       │                       │       │
   ▼                       ▼                       ▼       ▼
   ┌──────────────────┐    │                       │       │
   │ 跨 run 注入阶段    │    │                       │       │
   │ 裁剪 + Session    │    │                       │       │
   │ Memory 补偿       │    │                       │       │
   └──────────────────┘    │                       │       │
                           ┌───────────────────┐   │       │
                           │ Run 内 stage 1     │   │       │
                           │ 纯结构化裁剪        │   │       │
                           │ (有上面的摘要兜底)  │   │       │
                           └───────────────────┘   │       │
```

## Goals / Non-Goals

**Goals:**

- 让 `build_history_for` 的裁剪行为变为 ratio 感知：ratio < 0.65 时全量注入不裁剪
- 在裁剪触发时（或 Session Memory 存在但未裁剪时），注入 Session Memory 摘要作为上下文补偿
- 与 run 内压缩（0.70）形成 0.05 的缓冲带，让 LLM 在 run 内压缩不得不触发前手里已有摘要兜底

**Non-Goals:**

- 不改 run 内压缩 pipeline 的阈值和策略（0.70/0.80/0.88/0.93/0.95 不变）
- 不改 `compact_conversation` 的三路分支逻辑
- 不改 auto-compact hook 的触发条件
- 不改 Session Memory 的提取逻辑和触发条件
- 不在跨 run 注入阶段引入 LLM 调用（不触发 `compact_conversation`）
- 不让 Session Memory 作为消息加载的 cut-off（只做补充注入）
- 不改 `prune_old_tool_results` / `fold_old_messages` 的内部裁剪策略
- 不动态化 `DEFAULT_MAX_TURNS`（20）和 `AUTO_COMPACT_WATERMARK`（30）——这是独立议题

## Decisions

### D1. 阈值 0.65 硬编码，不配置化

**选择**：`PRE_RUN_COMPACT_RATIO = 0.65` 作为模块级常量。

**替代**：提取到 Settings 配置化。

**理由**：与 run 内压缩的 stage 1 阈值（0.70）形成 0.05 的缓冲带。如果后续需要按模型调整，可以提取到 Settings，但当前先保持简单。

### D2. Session Memory 注入优先级低于 ContextSummary

**选择**：有 ContextSummary 时不注入 Session Memory。

**替代**：两者同时注入。

**理由**：ContextSummary 是正式的 LLM 压缩产物，覆盖范围明确（`covered_until_message_id`），且 `build_history_for` 已经用它的 `covered_until_created_at` 作为消息加载的 cut-off。Session Memory 的覆盖范围是增量推进的，不如 ContextSummary 精确。两者同时注入会造成信息重复。

### D3. Session Memory 不作为消息加载的 cut-off

**选择**：注入 Session Memory 时不改变消息加载的 `where` 条件（不跳过 `covers_up_to` 之前的消息）。

**替代**：用 Session Memory 的 `covers_up_to` 做消息加载 cut-off，类似 ContextSummary。

**理由**：Session Memory 的 `covers_up_to` 是增量提取的副产物，不保证精确——提取时可能漏消息或延迟。如果用它做 cut-off 跳过消息，可能导致部分消息永久丢失。ContextSummary 做了断点保护（`_find_safe_cut_point`），Session Memory 没有。所以 Session Memory 只作为**补充上下文**注入，不影响消息加载范围。这意味着 `covers_up_to` 之前的消息可能同时出现在摘要和原始消息中——这是可接受的冗余，宁可信息重复也不可丢失。

### D4. ratio 未知时不裁剪

**选择**：当 `model_context_limit` 为 None 或 0 时，ratio = 0.0，走全量注入路径。

**替代**：ratio 未知时保守裁剪。

**理由**：无法判断 context 余量时，保守策略是全量注入——让 run 内压缩（有 ratio 判断）来兜底，而不是在跨 run 阶段盲目裁剪。

### D5. 不引入 LLM 调用

**选择**：跨 run 注入阶段不触发 `compact_conversation`，只读已有的 Session Memory。

**替代**：在 ratio 高但 Session Memory 不存在时，触发一次 `compact_conversation` 生成摘要。

**理由**：`build_history_for` 在 run 的关键路径上（用户等待响应），引入 LLM 调用会增加不可控延迟。Session Memory 是后台异步维护的，读取是纯 DB 查询，不引入延迟。如果 Session Memory 不存在，本次 run 退化为"裁剪无补偿"，下次 run 时 Session Memory 大概率已经有了。

### D6. Session Memory 使用独立标签 `<session_memory>`

**选择**：Session Memory 注入为 `<session_memory>` 块，不复用 ContextSummary 的 `<conversation_summary>` 标签。

**替代**：统一使用 `<conversation_summary>` 标签。

**理由**：两者的覆盖语义不同——ContextSummary 有精确的 `covered_until_message_id` + 断点保护，同时作为消息加载的 cut-off；Session Memory 的 `covers_up_to` 是增量提取的 timestamp 副产物，无断点保护，不做 cut-off。用不同标签避免 LLM 将两者混淆为同一级别的摘要。

### D7. Token 估算使用 `estimate_full_message_tokens` 而非序列化后估算

**选择**：ratio 计算使用 `estimate_full_message_tokens(merged)` 直接作用于 DB Message 对象，不先序列化为 dict 再估算。

**替代**：先 `_serialize_message` 为 dict，再 `estimate_dict_message_tokens` 估算。

**理由**：（1）避免双倍序列化开销——裁剪前估算一次 + 裁剪后序列化输出一次；（2）`estimate_full_message_tokens` 已被 Session Memory（`should_extract`）和 auto-compact（`estimate_uncompacted_tokens`）用于同类 ratio 判断，接口稳定；（3）它统计全量 parts（含 thinking），而序列化时丢弃 thinking（spec 13 规定跨 run 不回放 thinking），因此估算值略高于实际注入 token——这是安全偏向，宁可早裁不可晚裁。

## Risks / Trade-offs

- **[ratio 估算偏高]** `estimate_full_message_tokens` 统计含 thinking 的全量 parts，而序列化时丢弃 thinking → 估算值略高于实际注入 token → 可能在不该裁的时候提前裁。**缓解**：安全偏向（宁可早裁不可晚裁），且 thinking 通常占比小（< 5%）。

- **[Session Memory 覆盖范围与原始消息重叠]** D3 决定不做 cut-off，导致 `covers_up_to` 之前的消息同时出现在摘要和原始消息中 → 信息冗余 → 浪费少量 token。**缓解**：冗余只在 ratio < 0.65（不裁剪）时发生，此时 token 预算充足；ratio ≥ 0.65 裁剪后旧消息被 marker 替换，冗余消失。

- **[Session Memory 不存在时无补偿]** 首次 run 或 `_generate_fn` 不可用时 Session Memory 不存在 → 裁剪后无摘要兜底。**缓解**：退化为现状行为（裁剪无补偿），下次 run 时 Session Memory 大概率已存在（触发条件：10K token 后每 5K 增量或 3 次工具调用）。

- **[`DEFAULT_MAX_TURNS = 20` 限制加载条数]** 本设计让裁剪 ratio 感知，但 DB 查询 LIMIT 仍然是 20。如果历史超过 20 条但 ratio < 0.65，第 21 条及以后的消息不会被加载。**缓解**：这是独立议题，需要单独评估 1M context 下的合理 LIMIT 值，不在本变更范围内。
