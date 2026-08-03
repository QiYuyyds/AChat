## Context

AChat 的短期记忆滑动窗口由六层机制叠加构成。ratio-aware 裁剪（`PRE_RUN_COMPACT_RATIO = 0.65`，已实现）让**裁剪层**变为比例驱动，但**加载层**（`DEFAULT_MAX_TURNS = 20` 的 DB LIMIT）和**压缩触发层**（`AUTO_COMPACT_WATERMARK = 30` 的消息数 trigger）仍是 200K 时代的硬编码绝对数字。

在 1M context 模型（DeepSeek V4）下，这两个数字导致两个失配：
- `LIMIT 20`：20 条消息 ≈ 100K tokens，只占 1M context 的 10%，白白浪费 90% 上下文空间
- `WATERMARK 30`：30 条消息 ≈ 150K tokens = 15% of 1M，远未到 87% 就触发 LLM 压缩，浪费一次不必要的 LLM 调用

更糟的是两者**级联叠加**：watermark 在第 30 条触发压缩 → 生成 ContextSummary → 下次加载 cut-off 后可能只剩 10~15 条 → `LIMIT 20` 都没碰到 → 动态化 LIMIT 也白做。

本变更移除这两个硬编码数字，让 token budget（`context_window - output_reserve - prompt_estimate`）成为窗口的唯一约束，auto-compact 只由 87% token trigger 驱动。

## Goals / Non-Goals

**Goals:**

- 移除 `DEFAULT_MAX_TURNS = 20`，跨 run 历史加载不再有条数限制
- 移除 `AUTO_COMPACT_WATERMARK = 30`，auto-compact 只由 87% token trigger 触发
- token budget 兜底（已有逻辑）成为限制发给 LLM 内容的唯一硬约束
- 消除 1M context 下不必要的过早压缩和不必要的信息丢失

**Non-Goals:**

- 不改 ratio-aware 裁剪逻辑（0.65 阈值、prune_old_tool_results、fold_old_messages）
- 不改 run 内压缩 pipeline 的阈值和策略（0.70/0.80/0.88/0.93/0.95）
- 不改 `compact_conversation` 的三路分支逻辑（ContextSummary / SessionMemory / LLM）
- 不改 `KEEP_RECENT_MESSAGES = 6`（LLM 压缩后保留的原始消息条数，这是 UX 决策）
- 不改 SessionMemory 提取逻辑和触发条件
- 不改 `estimate_uncompacted_tokens` 的实现（只改调用频率）
- 不修复 `_build_history_with_assembler` 的双查询问题（独立议题）
- 不改 `count_uncompacted_messages` 函数定义（保留供 logging 使用，但不再作为 trigger 条件）
- 不改 `BuildHistoryOptions.max_turns` 字段定义（保留字段供外部调用方显式覆盖，默认 None = 不限制）

## Decisions

### D1. 移除 DB 查询的 LIMIT，依赖 ContextSummary cut-off + token budget 兜底

**选择**：删除 `_build_history_legacy` 和 `_build_history_with_assembler` 中 `.limit(max_turns)` 调用。

**理由**：去掉 LIMIT 后，DB 查询加载所有 uncompacted 消息。两个机制保证不会无限增长：
1. ContextSummary cut-off：`WHERE created_at > summary.covered_until_created_at`，已有逻辑，保证只加载上次压缩后的新消息
2. Token budget 兜底：`while total > token_budget: drop oldest non-pinned`，已有逻辑，保证发给 LLM 的内容在 budget 内

87% token trigger 保证 ContextSummary 会定期生成，cut-off 会定期推进。因此 uncompacted 消息数被 87% threshold 天然限制——在 1M context 下最多 ~174 条（≈870K tokens），在 8K context 下最多 ~7-14 条。

**替代方案**：动态 LIMIT（如 `max_turns = context_window // 5000`）。被否决因为：（1）token 估算不准（每条消息 1K~50K tokens 差异巨大），（2）和 watermark 级联后动态 LIMIT 可能被 cut-off 吃掉。

### D2. 移除消息数 trigger，只保留 87% token trigger

**选择**：删除 `_maybe_auto_compact_hook` 中的 `if watermark >= AUTO_COMPACT_WATERMARK` 分支，只保留 `if estimated_tokens > token_threshold` 分支。

**理由**：87% token trigger 是比例驱动的，天然适配所有 context window 大小。消息数 trigger 是 200K 时代的兜底，在 1M 下过早触发。去掉后：
- 8K 模型：87% × 8K = 6,960 tokens → ~7-14 条触发（和原来差不多，因为 87% 先于 30 触发）
- 1M 模型：87% × 1M = 870K → ~174 条触发（不再在第 30 条误触发）

### D3. 保留 `count_uncompacted_messages` 函数但不再作为 trigger

**选择**：不删除 `count_uncompacted_messages` 函数，但 `_maybe_auto_compact_hook` 不再调用它作为 trigger 条件。

**理由**：该函数在其他地方可能被用于 logging 和可观测性。删除函数会导致 import 链断裂和测试失败。保留函数定义，但去掉 trigger 调用。

### D4. agent_id 缺失时 auto-compact 不触发

**选择**：不添加 fallback。当 `agent_id` 为 None 时，auto-compact 不触发，日志记录 warning。

**理由**：正常 SDK agent run 总是传入 `agent_id`（调用链 `execute_run` → `asyncio.create_task(_maybe_auto_compact_hook(conv_id, override_prompt, args.agent_id))`）。CLI agent 走 session resume，不依赖 auto-compact。子 agent 被 `override_prompt` guard 跳过。`agent_id = None` 只在测试或边缘情况出现，添加 fallback 会掩盖配置错误。

### D5. `BuildHistoryOptions.max_turns` 保留但默认 None

**选择**：保留 `max_turns: int | None = None` 字段，默认 None = 不限制。调用方显式传入时仍生效（`.limit(max_turns)` 在 `max_turns is not None` 时执行）。

**理由**：外部调用方（如测试、特殊场景）可能需要限制加载条数。保留字段提供逃生舱。默认 None 时跳过 `.limit()` 调用。

## Risks / Trade-offs

- **`estimate_uncompacted_tokens` 每次都跑**：移除消息数 short-circuit 后，87% 检查在每次 run 结束后执行。该函数加载全部 uncompacted 消息并遍历 parts 估算 token。在 1M context 下最多 ~174 条消息，SQLite 查询 < 10ms + token 估算 ~50ms。后台 `asyncio.create_task`，不在关键路径。→ 可接受

- **无 ContextSummary 的长对话首次加载**：新对话没有 ContextSummary，如果积累了 174 条消息但 87% 还没触发（实际已接近触发），`build_history_for` 加载全部 174 条 → ratio 估算 → prune + fold → token budget 兜底。不会 OOM，只是多加载后裁剪。→ 可接受

- **agent_id 缺失时 auto-compact 不触发**：见 D4。→ 正常路径不受影响，边缘情况接受降级

- **测试需要重写**：`test_auto_compact_hook.py` 中 5 个测试依赖消息数 trigger，需要删除或重写为 87% token trigger 测试。→ 预期内工作量
