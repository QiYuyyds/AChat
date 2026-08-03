## 1. BuildHistoryOptions 扩展

- [x] 1.1 在 `backend/app/services/conversation_context.py` 的 `BuildHistoryOptions` dataclass 中新增 `model_context_limit: int | None = None` 字段（用于 ratio 计算）
- [x] 1.2 在 `BuildHistoryOptions` 中新增 `prompt_estimate: int = 0` 字段（system + prompt + safety 的 token 估算，由调用方传入）
- [x] 1.3 在 `backend/app/services/agent_runner.py` 的 `build_adapter_input` 中，将已有的 `limits.context_window` 和 `prompt_estimate` 传入 `BuildHistoryOptions` 构造参数

## 2. Ratio 判断 + 条件裁剪

- [x] 2.1 在 `conversation_context.py` 模块级新增 `PRE_RUN_COMPACT_RATIO = 0.65` 常量
- [x] 2.2 在 `_build_history_legacy` 中，`db.expunge_all()` 之后、`prune_old_tool_results` 之前，使用 `estimate_full_message_tokens(merged)` 估算 loaded_tokens
- [x] 2.3 计算 ratio = `(loaded_tokens + prompt_estimate) / model_context_limit`；当 `model_context_limit` 为 None 或 0 时 ratio = 0.0
- [x] 2.4 将 `prune_old_tool_results(merged)` 和 `fold_old_messages(merged, ...)` 的无条件调用改为 `if ratio >= PRE_RUN_COMPACT_RATIO:` 条件执行
- [x] 2.5 从 `app.services.transcript_renderer` 导入 `estimate_full_message_tokens`

## 3. Session Memory 注入

- [x] 3.1 在 `conversation_context.py` 中导入 `SessionMemory`（从 `app.memory.session_memory`）
- [x] 3.2 在 `_build_history_legacy` 的 items 构建阶段，在现有 ContextSummary 注入逻辑之后新增 `elif` 分支：当 `latest_summary is None` 时读取 SessionMemory
- [x] 3.3 调用 `await SessionMemory().get(conversation_id)` 获取 Session Memory 记录（`get()` 是纯 DB 查询，不依赖 `_generate_fn`）
- [x] 3.4 当 Session Memory 存在且 `summary` 非空时，构建 `<session_memory covers_up_to="...">` 块并作为 `_Item(msg_id="session_memory", is_pinned=True, ...)` 注入到 items 列表头部
- [x] 3.5 确保 ContextSummary > SessionMemory 的优先级：有 ContextSummary 时只注入 `<conversation_summary>`，不注入 `<session_memory>`

## 4. 单元测试

- [x] 4.1 测试 ratio < 0.65 时 `prune_old_tool_results` 和 `fold_old_messages` 不被调用，tool_result 完整保留
- [x] 4.2 测试 ratio ≥ 0.65 时裁剪正常执行（和现有行为一致）
- [x] 4.3 测试 `model_context_limit` 为 None 时 ratio = 0.0，走全量注入路径
- [x] 4.4 测试 Session Memory 存在但无 ContextSummary 时，`<session_memory>` 块被注入到 items 头部且标记为 pinned
- [x] 4.5 测试 ContextSummary 和 Session Memory 同时存在时，只注入 ContextSummary 块
- [x] 4.6 测试 Session Memory 不存在且无 ContextSummary 时，不注入任何摘要块
- [x] 4.7 ORM 安全回归：ratio < 0.65 跳过 prune 时，DB 中的 `Message.parts_list` 不被修改

## 5. 回归验证

- [x] 5.1 `cd backend && ruff check .` 无新增错误
- [x] 5.2 `cd backend && pytest` conversation_context + compaction 相关用例全绿
- [ ] 5.3 端到端验证：短对话（ratio < 0.65）跨 run 时历史完整注入，LLM 不需要重新调工具获取信息
- [ ] 5.4 端到端验证：长对话（ratio ≥ 0.65）跨 run 时裁剪正常，Session Memory 摘要作为补偿注入
- [ ] 5.5 端到端验证：run 内压缩（stage 1 at 0.70）行为不受影响
