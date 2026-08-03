# Proposal: Add Effective Context Window

## Why

项目的模型注册表将 DeepSeek 的物理上下文窗口（1M tokens）直接用作 token budget 计算的上限。业界研究表明，LLM 在超过 ~200K tokens 后出现明显的质量退化（Lost in the Middle、Context Rot），且成本与延迟非线性增长。Claude Code 等主流 Agent 框架即使在 1M 上下文可用时也默认使用 200K 作为工程有效窗口。我们需要将物理窗口与工程有效窗口分离，将有效上下文统一 cap 到 200K，以提升 Agent 输出质量并控制成本。

## What Changes

- 在 `ModelLimits`（后端 Python + 前端 TypeScript）中新增 `effective_context_window` 字段，与物理 `context_window` 分离
- 新增全局常量 `EFFECTIVE_CONTEXT_CAP = 200_000`，`effective_context_window = min(context_window, EFFECTIVE_CONTEXT_CAP)`
- 所有 token budget 计算（`history_budget`、`model_context_limit`、auto-compact 阈值）从使用 `context_window` 改为使用 `effective_context_window`
- DeepSeek 全系列模型的 `outputReserve` 统一调整为 `13_000`（学习 Claude Code 的 `AUTOCOMPACT_BUFFER_TOKENS = 13_000`）
- 前端 `UsageBadge` 的上下文进度条分母从物理窗口改为有效窗口
- 物理窗口 `context_window` 保留在注册表中，不丢失模型能力元信息（用于定价展示等）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `conversation-context`: token budget 计算从物理上下文窗口改为有效上下文窗口（`effective_context_window`），影响 `history_budget`、`model_context_limit`、auto-compact 触发阈值

## Impact

- **后端**：`backend/app/utils/model_registry.py`（ModelLimits 数据结构 + get_model_limits 逻辑 + DeepSeek outputReserve）、`backend/app/services/agent_runner.py`（history_budget / model_context_limit / _get_agent_model_limit 三处引用）
- **前端**：`src/shared/model-registry.ts`（镜像同步）、`src/components/usage-badge.tsx`（进度条分母）
- **测试**：`backend/tests/test_model_registry.py`、`src/shared/model-registry.test.ts`（断言更新）
- **无 breaking change**：`effective_context_window` 是新增字段，旧调用方若仍用 `context_window` 不报错，只是 budget 更保守
- **CLI Agent 不受影响**：Claude Code / Codex 走 CLI 子进程，不经过 `model_registry.py` 的 budget 计算
