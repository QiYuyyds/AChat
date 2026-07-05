## ADDED Requirements

### Requirement: Context summary SHALL auto-refresh on a message watermark

当未压缩消息水位（`COUNT(messages WHERE created_at > last_context_summary.covered_until_created_at)`）达到阈值 10 时，系统 MUST 在该轮 run 结束后自动触发 `compact_conversation`，不再依赖用户手动 `/compact`。自动触发仅提供外层水位，内层仍由 `compact_conversation` 的 `MIN_COMPACT_TOKENS` / `MIN_COMPACTABLE` gate 兜底。

#### Scenario: watermark reached triggers auto-compaction

- **WHEN** 一轮 run 结束后，未压缩消息计数 ≥ 10
- **THEN** `_maybe_auto_compact_hook` 调用 `compact_conversation(silent=True)`，生成新的 `ContextSummary` 行。

#### Scenario: watermark not reached skips auto-compaction

- **WHEN** 一轮 run 结束后，未压缩消息计数 < 10
- **THEN** 不触发自动压缩，不调用 `compact_conversation`。

#### Scenario: watermark reached but slice too small

- **WHEN** 未压缩消息计数 ≥ 10 但待压缩切片 token 估算 < `MIN_COMPACT_TOKENS`（800）
- **THEN** `compact_conversation` 抛出 `CompactionSkipped`，不写新 summary，hook 静默吞掉异常。

### Requirement: Auto-compaction SHALL be silent

自动触发的压缩 MUST NOT 插入"已将 N 条历史消息压缩为上下文摘要"的 role=system 消息，MUST NOT 广播 `MessageAddedEvent`；它 MUST 仅持久化 `ContextSummary` 行。静默压缩通过 `compact_conversation(silent=True)` 实现。

#### Scenario: auto-compaction produces no chat message

- **WHEN** 自动压缩成功执行
- **THEN** 对话流中不出现"已压缩"系统消息，前端聊天面板无新增消息条目。

#### Scenario: auto-compaction still persists summary

- **WHEN** 自动压缩成功执行
- **THEN** `ContextSummary` 表新增一行，`get_latest_context_summary` 在下一轮 `build_history_for` 时返回该行。

### Requirement: Manual compaction SHALL retain the announcement

用户手动触发的 `/compact` MUST 保留现有行为：插入 role=system 公告消息并广播 `MessageAddedEvent`。手动路径走 `compact_conversation(silent=False)`（默认）。

#### Scenario: manual compact shows announcement

- **WHEN** 用户通过 `/compact` 显式触发压缩
- **THEN** 对话流出现"已将 N 条历史消息压缩为上下文摘要…"系统消息，且 `MessageAddedEvent` 被广播。

### Requirement: Auto-compaction SHALL be guarded against sub-agent runs

当 `args.override_prompt` 非空（Orchestrator 派发的子任务 run）时，自动压缩 MUST NOT 触发，避免子任务副作用整条会话的上下文。

#### Scenario: child run skips auto-compaction

- **WHEN** 一轮 run 的 `override_prompt` 非空且未压缩消息水位达标
- **THEN** `_maybe_auto_compact_hook` 不调用 `compact_conversation`。

### Requirement: compact_conversation SHALL support a silent parameter

`compact_conversation` MUST 接受 `silent: bool = False` 参数。当 `silent=True` 时，跳过"步骤 i"（系统消息插入与事件广播），仅执行"步骤 a-h"（核心压缩：加载、gate、LLM 调用、持久化 `ContextSummary`）。`CompactResult.message` 在 silent 模式下为 `None`。

#### Scenario: silent call skips system message

- **WHEN** `compact_conversation(silent=True)` 被调用并成功
- **THEN** 无 role=system 消息被插入，无 `MessageAddedEvent` 被广播，`CompactResult.message` 为 `None`，`CompactResult.summary` 与 `ctx_before`/`ctx_after` 仍正常返回。

#### Scenario: non-silent call retains system message

- **WHEN** `compact_conversation()` 或 `compact_conversation(silent=False)` 被调用并成功
- **THEN** role=system 公告消息被插入并广播（保持现有行为）。

### Requirement: Auto-compaction SHALL run as a non-blocking post-run task

`_maybe_auto_compact_hook` MUST 作为 `execute_run` 的 `asyncio.create_task` 后置执行，MUST NOT 阻塞主对话流；其异常 MUST 被捕获并记录为 warning，不影响 run 的最终状态。

#### Scenario: auto-compaction failure does not fail the run

- **WHEN** `_maybe_auto_compact_hook` 内部抛出异常（含 `CompactionSkipped`）
- **THEN** 该异常被 best-effort 捕获并记录，触发它的 run 仍以 `complete` 状态结束。

#### Scenario: async race is tolerated

- **WHEN** 自动压缩异步执行尚未完成时用户发送下一轮
- **THEN** 下一轮 `build_history_for` 可能读到旧 summary 或新 summary（竞态），系统 MUST 不出错，缓存 miss 落到读取到新 summary 的那一轮。
