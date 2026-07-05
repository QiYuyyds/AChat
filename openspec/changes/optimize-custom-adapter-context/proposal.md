## Why

custom_adapter（SDK 路径，基于 AsyncOpenAI 兼容协议，依赖 API 自动识别最长公共前缀的隐式缓存）存在两个上下文缺口：

1. **无会话元数据注入**——模型对语言、时区、时间毫无感知；且静态元数据无法加入缓存稳定前缀，隐式缓存命中率未被充分压榨。
2. **上下文压缩仅手动触发**——`compact_conversation`（写 `ContextSummary` 表、注入 `history[0]`）只在用户手动 `/compact` 时执行；长会话历史无界增长，直到用户主动压缩，token 成本与延迟随轮次线性攀升。

两个缺口都让 OpenAI 兼容协议的隐式前缀缓存机制未能发挥最大效力。

## What Changes

- **新增 Session Metadata 三字段钝化注入**：`language`（如 `zh-CN`）、`timezone`（如 `GMT+8`）、`current_time`（钝化为 time_bucket，如 `Sunday_Morning`）。静态两项（language/timezone）注入 `system_prompt` 加入缓存稳定前缀；动态一项（current_time）注入尾部 user 消息，不进缓存区。
- **新增 Recent Summary 自动周期化**：当未压缩消息水位（`COUNT(msg.created_at > last_summary.covered_until_created_at)`）≥ 10 时自动触发 `compact_conversation`，复用其内层 `MIN_COMPACT_TOKENS=800` / `MIN_COMPACTABLE=2` 兜底。
- **新增静默压缩模式**：给 `compact_conversation` 增加 `silent: bool = False` 参数；`silent=True` 时跳过"已压缩"系统消息插入与广播，仅执行核心压缩。手动 `/compact` 保留公告，自动触发走静默。
- **新增 post-run 钩子**：`_maybe_auto_compact_hook` 作为 `execute_run` 的 `asyncio.create_task` 后置执行；`args.override_prompt` 非空（子 Agent run）时豁免。
- **不改动**：Profile（保持 `static=False` 进动态 `<system-reminder>`）、`cache_control` 显式断点（OpenAI 兼容协议无此字段）、CLI 适配器链路。

## Capabilities

### New Capabilities

- `session-metadata`: 钝化会话元数据（language / timezone / current_time）注入 custom_adapter 拼装的 prompt；定义静态/动态归属规则与钝化分桶规范，以提升隐式前缀缓存命中率并赋予模型语言/时区/时间感知。

### Modified Capabilities

- `conversation-context`: `ContextSummary` 不再仅手动触发——新增基于水位计数的自动周期压缩与静默模式；`history[0]` 的 summary 内容将在自动压缩后变更（Replace 策略，下一轮缓存 miss 一次后重新稳定）。

## Impact

- `backend/app/services/context_compaction_service.py`：`compact_conversation` 增加 `silent` 参数，拆分核心压缩（步骤 a-h）与公告（步骤 i）。
- `backend/app/services/agent_runner.py`：新增 `_maybe_auto_compact_hook` 后置钩子（`execute_run` 第 597 行附近）；`build_adapter_input` 注入 session metadata。
- `backend/app/services/conversation_context.py`：summary 注入逻辑复用，无结构性改动。
- `backend/app/services/prompt_assembler.py`：无改动（design D7 定：metadata 在 `build_adapter_input` 直接拼装，不经 PromptAssembler）。
- `backend/app/config.py`：新增 `default_language`/`default_timezone` 两个 Settings 字段（见 design D7），走 `.env` / `.env.local`。
- `backend/app/adapters/custom_adapter.py`：无改动（消费已拼装的 `AdapterInput`）。
- **无 DB schema 变更**：`ContextSummary` 表复用；session metadata 为运行时计算值，不持久化。
- **无破坏性 API 变更**：手动 `/compact` 行为保持不变。
