## 1. 移除 DEFAULT_MAX_TURNS（conversation_context.py）

- [x] 1.1 删除模块级常量 `DEFAULT_MAX_TURNS = 20`（line 53）
- [x] 1.2 在 `_build_history_legacy` 中：将 `max_turns = opts.max_turns if opts.max_turns is not None else DEFAULT_MAX_TURNS` 改为 `max_turns = opts.max_turns`，并将 `.limit(max_turns)` 改为条件调用（`if max_turns is not None: recent_stmt = recent_stmt.limit(max_turns)`）
- [x] 1.3 在 `_build_history_with_assembler` 中：同 1.2 的改法（line 374 和 line 389）
- [x] 1.4 更新 `BuildHistoryOptions.max_turns` 字段注释为 `# How many recent (non-pinned) messages to load. None → no limit (load all uncompacted).`

## 2. 移除 AUTO_COMPACT_WATERMARK（context_compaction_service.py）

- [x] 2.1 删除 `AUTO_COMPACT_WATERMARK = 30` 常量及其上方注释块（line 44-49）
- [x] 2.2 更新 `count_uncompacted_messages` 函数 docstring：移除 "watermark used by _maybe_auto_compact_hook" 的表述，改为 "count of uncompacted messages (informational only)"

## 3. 简化 _maybe_auto_compact_hook（agent_runner.py）

- [x] 3.1 删除消息数 trigger 分支：`watermark = await count_uncompacted_messages(...)` 及 `if watermark >= AUTO_COMPACT_WATERMARK:` 整个 block（line 346-364）
- [x] 3.2 从 import 中移除 `AUTO_COMPACT_WATERMARK`（line 57 或附近的 from import 块）
- [x] 3.3 从 import 中移除 `count_uncompacted_messages`（如果不再被其他地方引用）
- [x] 3.4 在 87% token trigger 分支前添加：当 `agent_id` 为 None 时 log warning 并 return
- [x] 3.5 更新函数 docstring：将 "Triggers when either: message count ≥ watermark OR tokens > 87%" 改为 "Triggers when estimated tokens > 87% of the model's context window"

## 4. 更新测试

- [x] 4.1 在 `test_auto_compact_hook.py` 中删除以下消息数 trigger 测试：`test_watermark_reached_triggers_silent_compact`、`test_watermark_below_threshold_does_not_trigger`、`test_watermark_exactly_at_threshold_triggers`、`test_compaction_skipped_is_swallowed`（改为 87% trigger 版本）、`test_general_exception_does_not_propagate`（改为 87% trigger 版本）、`test_override_prompt_non_empty_exempts`（保留但移除 `count_uncompacted_messages` mock）
- [x] 4.2 保留并增强 `test_token_threshold_triggers_compaction`：验证不再需要 `mock_count` mock（或保留但断言它不被调用）
- [x] 4.3 保留并增强 `test_neither_threshold_met_does_not_compact`：同上
- [x] 4.4 重写 `test_no_agent_id_falls_back_to_watermark_only` 为 `test_no_agent_id_does_not_trigger`：验证 `agent_id=None` 时 auto-compact 不触发且 warning 被 logged
- [x] 4.5 在 `test_ratio_aware_pruning.py` 中新增测试：无 LIMIT 时全量加载消息，token budget 兜底丢弃最旧消息
- [x] 4.6 在 `test_ratio_aware_pruning.py` 中新增测试：显式 `max_turns=5` 时 DB 查询仍应用 LIMIT 5

## 5. 回归验证

- [x] 5.1 `cd backend && ruff check .` 无新增错误
- [x] 5.2 `cd backend && pytest` conversation_context + auto_compact 相关用例全绿
- [ ] 5.3 端到端验证：1M context 模型短对话（< 87% token）跨 run 时不触发 auto-compact，历史全量加载
- [ ] 5.4 端到端验证：1M context 模型长对话（≥ 87% token）跨 run 时触发 auto-compact，ContextSummary 正确生成
- [ ] 5.5 端到端验证：8K context 模型短对话行为与改动前一致（87% trigger 先于旧 30 条触发）
