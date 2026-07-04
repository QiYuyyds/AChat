## Context

当前 PromptAssembler 将所有槽位（Constraints、Profile、Planner、TaskMem、ToolState、Recall）统一通过 `render_system_prompt()` 拼接到 system prompt。由于 Planner/TaskMem/ToolState/Recall 每轮都变，system prompt 前缀不稳定，prompt cache 命中率接近 0%。

Compaction 的 `_summarise()` 使用完全独立的单条 user message 调用 LLM，不复用主对话的 system prompt，导致 10 万 token 对话的压缩调用全价计费。

项目在 `custom_adapter.py` 中已有逐请求级别的 cache token 追踪（`_RunUsage`/`_MsgUsage`），但 `cache_creation_tokens` 字段定义后从未赋值，且无聚合级监控。

## Goals / Non-Goals

**Goals:**

- system prompt 前缀稳定性：同会话内缓存命中率从 ~0% 提升到 ~70-80%
- compaction LLM 调用成本降低 80-90%（前缀命中缓存）
- RecallSource 改为 Agent 按需触发，减少无效 embedding 调用
- cache 命中率可观测：聚合监控 + 告警 + API 端点

**Non-Goals:**

- 工具延迟加载（Phase 2 DEFERRED）：当前 MCP 工具数为 0，暂不实施
- 跨会话缓存共享：需要 provider 层面支持，不在本次范围内
- CLI Agent（Claude/Codex）的 prompt caching：CLI Agent 自管理上下文，不经过 PromptAssembler

## Decisions

### D1: 静态/动态槽位按变化频率分类，而非按功能分类

**选择**：按变化频率将 6 个槽位分为 static（Constraints、Profile）和 dynamic（Planner、TaskMem、ToolState、Recall）。

**理由**：Claude Code 团队的核心原则是"前缀中任何一处改动都会让其后所有缓存失效"。按变化频率分类能最大化前缀稳定性——static 槽位几乎不变，dynamic 槽位每轮都变但注入到 user message 不破坏 system prompt 缓存。

**替代方案**：按功能分类（安全相关 vs 上下文相关），但这样 Profile 会被归为动态，失去跨会话缓存机会。

### D2: 动态内容注入为 `<system-reminder>` 包裹的 user message 前缀

**选择**：动态内容通过 `<system-reminder>\n{ctx}\n</system-reminder>` 包裹，拼接到 user message 前面。

**理由**：`<system-reminder>` 是 LLM 能识别的语义标签，表示"系统级上下文注入但不改变 system prompt"。这样 dynamic 内容虽然每轮都变，但只影响 user message 部分，system prompt 前缀保持稳定。

**替代方案**：注入为独立的 system 角色消息放在 history 中，但这样会改变消息序列结构，可能影响某些 provider 的缓存行为。

### D3: Compaction 复用 system prompt 但不传 tools

**选择**：`_summarise()` 接收 `parent_system_prompt` 参数，构造 `messages=[{system}, {user: compaction_prompt + transcript}]`，不传 `tools` 参数。

**理由**：
1. 复用 system prompt 使前缀命中缓存，降低 input 成本
2. 不传 tools 避免模型在压缩过程中尝试调用工具——compaction 是纯文本生成任务
3. 对比原方案的 `tools=parent_tools`：传 tools 既有工具调用风险，又增加 token 消耗

**替代方案**：原改进方案提出 `tools=parent_tools`，但这会让模型有可能在压缩时发起工具调用，需要额外处理 tool_call 响应，增加复杂度且不符合 compaction 的语义。

### D4: RecallSource 从 Schema 移除而非加开关控制

**选择**：直接从 4 种 Schema 中移除 `SlotRecall`，不保留可配置开关。

**理由**：
1. `memory_recall` 工具已存在且功能完整，Agent 可以自主决定何时回忆
2. 保留开关会增加配置复杂度，且默认开启时仍会破坏缓存
3. 移除后 RecallSource 类仍保留在代码中（注册逻辑不变），只是 Schema 不再引用它，未来需要时可快速恢复

**替代方案**：增加 `recall_on_demand: bool` 配置项，但增加了配置面且无法解决默认场景的缓存问题。

### D5: CacheMetrics 基于已有 `_RunUsage` 增量构建

**选择**：在 `custom_adapter.py` 的 `_RunUsage` 已有的 `cache_read_tokens` / `cache_creation_tokens` 字段基础上，新增 `CacheMetrics` 聚合器，在 `RunUsageEvent` 发射时调用 `cache_metrics.record()`。

**理由**：
1. 项目已有逐请求 cache token 追踪，不需要从零实现
2. `_RunUsage.cache_creation_tokens` 已定义但从未赋值——修复此 bug 同时完成 Anthropic API 的 `cache_creation_input_tokens` 提取
3. DeepSeek 的 `prompt_cache_hit_tokens` / `cached_tokens` 已在 L205 处理，只需补充 Anthropic 格式

**替代方案**：在 `custom_provider_client.py` 层拦截，但该文件只处理 client config，不参与 LLM 调用。

## Risks / Trade-offs

- **[动态内容注入 user message 可能影响模型理解]** → 使用 `<system-reminder>` 标签包裹，LLM 能区分系统上下文与用户输入；需验证 DeepSeek 和其他 OpenAI-compatible provider 的兼容性
- **[Compaction 不传 tools 可能略微降低缓存命中率]** → system prompt 仍然命中（占前缀大部分），仅 tools 部分不匹配，整体命中率仍远优于完全独立调用
- **[移除 RecallSource 后 Agent 可能忘记回忆]** → `memory_recall` 工具描述已足够明确（"当需要回忆过去的对话内容、用户偏好或历史事实时使用"），且工具调用结果会带回 preferences 上下文
- **[`cache_creation_tokens` 修复可能暴露 Anthropic 格式差异]** → 需同时处理 `cache_creation_input_tokens`（Anthropic）和已有的 `prompt_cache_hit_tokens`（DeepSeek）两种字段名
