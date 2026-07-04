## Why

PromptAssembler 的动态内容（TaskMem、ToolState、Recall 等）每轮都拼接到 system prompt，导致 prompt cache 前缀匹配失败、每轮全价计算。Compaction 调用使用完全独立的 prompt，无法命中主对话缓存。项目已有逐请求 cache token 追踪（`_RunUsage`/`_MsgUsage`），但缺少聚合级监控与告警。本变更将架构从"功能正确"转向"缓存友好"，在 system prompt 稳定性、compaction 成本、Recall 按需触发、cache 可观测性四个维度落地改进。

## What Changes

- **Slot 静态/动态分离**：`Slot` dataclass 新增 `static: bool` 字段；4 种 Schema 标注静态槽位（Constraints、Profile）与动态槽位（Planner、TaskMem、ToolState、Recall）；`RuntimeContext` 新增 `render_static()` 和 `render_dynamic()` 方法
- **动态内容注入通道迁移**：agent_runner.py 中 PromptAssembler 的动态内容从 system prompt 迁移到 user message 前缀（`<system-reminder>` 包裹）；conversation_context.py 的 `render_history()` 同步调整
- **RecallSource 从 Schema 移除**：4 种 Schema 中的 `SlotRecall` 移除，Agent 通过已有的 `memory_recall` 工具按需触发语义回忆
- **Cache-Safe Compaction**：`_summarise()` 复用主对话的 system prompt（不传 tools，避免模型在压缩时调用工具），仅末尾追加 compaction 指令，使前缀命中缓存
- **Cache 命中率可观测性**：基于已有的 `_RunUsage`/`_MsgUsage` 追踪层，新增 `CacheMetrics` 聚合器（滑动窗口、命中率告警）和 API 端点；修复 `cache_creation_tokens` 未赋值的 bug
- **工具延迟加载（DEFERRED）**：`ToolDef` 增加 `is_mcp`/`defer_loading` 标记、新增 `tool_search` 工具——标记为 Phase 2，当前 MCP 工具数为 0，暂不实施

## Capabilities

### New Capabilities

- `prompt-caching`: Prompt caching 架构能力——定义静态/动态槽位分离规范、cache-safe compaction 策略、cache 命中率可观测性（聚合监控 + 告警 + API 端点）

### Modified Capabilities

- `conversation-context`: PromptAssembler Schema 变更——Slot 增加 `static` 字段、新增 `render_static()`/`render_dynamic()` 方法、RecallSource 从 Schema 移除、动态内容注入通道从 system prompt 改为 user message
- `tools`: 工具延迟加载规范（Phase 2 DEFERRED）——`ToolDef` 增加 `is_mcp` 标记、`ToolRegistry.get_tool_defs(full)` 支持 stub 模式、新增 `tool_search` 工具

## Impact

- `backend/app/services/prompt_assembler.py` — Slot/Schema/RuntimeContext 核心变更
- `backend/app/services/agent_runner.py` — 注入逻辑从 system prompt 改为 user message
- `backend/app/services/conversation_context.py` — `render_history()` 路径同步调整
- `backend/app/services/context_compaction_service.py` — `_summarise()` 复用主对话 system prompt
- `backend/app/adapters/custom_adapter.py` — cache token 提取修复 + CacheMetrics 集成
- `backend/app/infra/cache_metrics.py` — 新增 CacheMetrics 聚合器
- `backend/app/api/settings.py` — 新增 `/cache-metrics` API 端点
- `backend/app/tools/registry.py` — （Phase 2）defer_loading 支持
- `backend/app/tools/tool_search.py` — （Phase 2）新增 tool_search 工具
