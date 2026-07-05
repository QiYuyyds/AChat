## 1. 前置确认（解决 design 的 Open Questions）

- [x] 1.1 确认 `language`/`timezone` 取值来源 → **Settings 环境变量**（`config.py` 新增 `default_language`/`default_timezone`），不透传 HTTP 头——见 design D7
- [x] 1.2 确认 `current_time` 取服务器时间还是客户端时间 → **服务器时间**（`datetime.now()`），拼装时实时计算——见 design D8
- [x] 1.3 确定 `time_bucket` 分桶粒度与边界 → **星期 + 四时段**（Morning 05–11 / Afternoon 12–17 / Evening 18–22 / LateNight 23–04），28 桶，18:00 归 `Evening`——见 design D9

## 2. 静默压缩基础设施（compact_conversation silent 参数）

- [x] 2.1 给 `compact_conversation` 增加 `silent: bool = False` 参数签名（`backend/app/services/context_compaction_service.py`）
- [x] 2.2 当 `silent=True` 时跳过"步骤 i"（第 258-305 行的系统消息插入 + `MessageAddedEvent` 广播），仅执行"步骤 a-h"核心压缩
- [x] 2.3 silent 模式下 `CompactResult.message` 置 `None`，`summary`/`ctx_before`/`ctx_after` 照常返回
- [x] 2.4 单元测试：`silent=True` 不插入系统消息、不广播事件、`message=None`；`silent=False` 保留现有公告行为

## 3. 自动压缩触发器（_maybe_auto_compact_hook）

- [x] 3.1 实现水位计数函数 `count_uncompacted_messages(conversation_id) -> int`：`COUNT(*) WHERE conversation_id=? AND status='complete' AND created_at > last_summary.covered_until_created_at`；无 last_summary 时计数全部 complete 消息
- [x] 3.2 实现 `_maybe_auto_compact_hook(conversation_id)`：水位 ≥ 10 时调 `compact_conversation(silent=True)`；捕获 `CompactionSkipped` 与一般异常，记 warning，不外抛
- [x] 3.3 在 `execute_run` 成功路径（第 597 行附近，与 `_post_run_memory_hook` 并列）注册 `asyncio.create_task(_maybe_auto_compact_hook(args.conversation_id))`
- [x] 3.4 guard：`args.override_prompt` 非空（子 Agent run）时不触发
- [x] 3.5 单元测试：水位达标触发 `compact_conversation(silent=True)`；未达标不触发；slice 太小走 `CompactionSkipped` 被吞；`override_prompt` 非空豁免；hook 异常不影响 run `complete` 状态

## 4. Session Metadata 钝化与注入

- [x] 4.1 实现钝化函数 `_blunt_metadata(language, timezone, current_time) -> tuple[str, str, str]`：language→locale 标签（`zh-CN`）；timezone→偏移标签（`GMT+8`）；current_time→`{Weekday}_{Period}`（见 D9：Morning 05–11 / Afternoon 12–17 / Evening 18–22 / LateNight 23–04）
- [x] 4.2 在 `build_adapter_input` 的 SDK 分支（`is_sdk`）：将 language/timezone 钝化标签拼入 `system_prompt_with_workspace`（加入缓存稳定前缀）
- [x] 4.3 在 `build_adapter_input` 的 SDK 分支：将 current_time 钝化分桶值拼入尾部 user 消息（`effective_prompt`，位于 `<system-reminder>` 块与用户真实输入之间或之后）
- [x] 4.4 CLI 分支（`is_cli`）不注入任何 metadata（guard）
- [x] 4.5 单元测试：static 项出现在 `system_prompt` 且不在 user 消息；dynamic 项出现在 user 消息尾部且不在 `system_prompt`；CLI 不注入；钝化形态正确（精确时间戳不出现在 prompt）

## 5. 集成、可观测与回归

- [x] 5.1 集成测试：构造 >10 轮 complete 消息的长会话，触发一次 run，断言新增一行 `ContextSummary` 且无 role=system "已压缩"消息
- [x] 5.2 集成测试：手动 `/compact` 路径仍产生 role=system 公告消息与 `MessageAddedEvent`
- [x] 5.3 在 cache-debug 日志中补充 metadata 注入记录（static_len/dynamic_len 之外加 metadata 标签）与 auto-compact 触发记录（水位值/silent/skipped reason）
- [x] 5.4 回归：现有 `tests/test_context_compact*.py`、`tests/test_conversation_context*.py`、`tests/test_agent_runner.py` 全部通过
- [x] 5.5 运行 `openspec status --change "optimize-custom-adapter-context"` 确认所有任务完成，准备 archive
