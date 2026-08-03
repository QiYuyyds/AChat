## Why

`build_history_for`（跨 run 历史注入）中的 `prune_old_tool_results` 和 `fold_old_messages` 是**无条件调用**的——不管 context window 还剩多少空间，只要完整 turn 数 > 2 就裁剪 tool_result，> 4 就折叠旧 turn。这意味着即使模型 context window 是 1M tokens、历史只占了 8%，旧 tool_result 也会被替换成粗糙的 marker，导致 LLM 在下一个 run 中看不到上一 run 的具体工具调用结果，可能需要重新调工具获取信息。

同时，Session Memory 在 Run 进行过程中已经在后台增量提取了高质量摘要，但 `build_history_for` 根本不读它——只有 `compact_conversation`（auto-compact / 手动 /compact）才使用，而 auto-compact 的触发阈值很高（30 条消息或 87% context window），在短对话场景下不会触发，Session Memory 白做了。

## What Changes

- `BuildHistoryOptions` 新增 `model_context_limit` 和 `prompt_estimate` 两个字段，由调用方（`agent_runner.py:build_adapter_input`）传入，用于计算历史占 context window 的 ratio
- 在 `_build_history_legacy` 中引入 `PRE_RUN_COMPACT_RATIO = 0.65` 常量：当 ratio < 0.65（或 model_context_limit 未知）时全量注入不裁剪；ratio ≥ 0.65 时执行现有的结构化裁剪（`prune_old_tool_results` + `fold_old_messages`）
- 裁剪后（或未裁剪但 Session Memory 存在时），读取并注入 Session Memory 摘要作为上下文补偿，使用 `<session_memory>` 标签与 ContextSummary 的 `<conversation_summary>` 做显式区分
- Session Memory 注入优先级低于 ContextSummary：有 ContextSummary 时不注入 Session Memory（避免两份摘要重复）
- 0.65 阈值与 run 内压缩 stage 1 的 0.70 阈值形成 0.05 的缓冲带：在 run 内压缩不得不触发之前，先在跨 run 注入阶段做一次有 Session Memory 补偿的裁剪

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `conversation-context`: `build_history_for` 的裁剪行为从无条件执行改为 ratio 感知（0.65 阈值门控）；新增 Session Memory 注入作为 ContextSummary 不存在时的上下文补偿

## Impact

- **后端代码**：修改 `backend/app/services/conversation_context.py`（`BuildHistoryOptions` 扩展 + `_build_history_legacy` ratio 判断 + Session Memory 注入）和 `backend/app/services/agent_runner.py`（`build_adapter_input` 传入 `model_context_limit` 和 `prompt_estimate`）
- **数据库**：无变更（Session Memory 存储在已有的 `context_summaries` 表 `summary_type='session'` 行中，本变更只读取不写入）
- **API**：无外部接口变更
- **前端**：无直接影响。`UsageBadge` 的「当前 ctx」进度条会间接反映更真实的 context 占用（改动前因无条件裁剪显示偏低，改动后短对话场景下进度条比例可能变高——这是正确行为）
- **SSE 事件**：不新增、不修改、不删除任何 SSE 事件类型
- **风险**：ratio 估算使用 `estimate_full_message_tokens` 统计 DB Message 全量 parts（含 thinking），而序列化时丢弃 thinking（跨 run 不回放），因此估算值略高于实际注入 token——这是安全偏向（宁可早裁不可晚裁）
