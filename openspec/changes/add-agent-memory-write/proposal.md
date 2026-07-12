## Why

当前 AChat 的记忆系统是**纯被动提取**模式——Agent 只能通过 `memory_recall` 工具读记忆，所有记忆写入都由系统后台的 `memory_writer.extract_memory_from_reply` 异步完成。Agent 无法主动记录"这个用户的项目用了 React 19"或"上次部署失败因为端口冲突"。

这导致两个问题：

- **记忆质量低**：后台提取器从 assistant 回复中抽取 k-v 事实，但 Agent 自己比提取器更知道什么值得记——提取器可能漏掉关键决策上下文，也可能记下无关细节
- **Agent 无自主性**：Agent 发现重要信息时无法主动持久化，只能"被动被记忆"。在多 Agent 协作场景下，子 Agent 发现的关键信息如果不在回复正文中出现，就永远不会被记住

Claude Code 的做法是：声明了 `memory` 的 Agent 自动获得 `FileWrite/FileEdit/FileRead` 工具，Agent 自己决定记什么、何时记、怎么更新。但 Claude Code 是纯文件方案，依赖模型遵守 prompt 指令来防"什么都写"。

AChat 作为多 Agent 平台，N 个 Agent 同时写入时"什么都写"的风险被放大——需要比 Claude Code 更强的硬约束。

## What Changes

**A. 新增 `memory_store` 工具**

- Agent 可主动调用 `memory_store(content, category, importance, tags)` 写入 LTM
- 工具描述明确指导"什么值得记 / 什么不值得记"
- 返回当前 Agent 的记忆数量，让 Agent 感知"是否需要节制"

**B. 三层防"什么都写"机制**

- **硬约束（handler 级）**：category 白名单（只接受 fact/policy/tool_failure）、importance 下限（≥0.3）、每轮写入限流（max 3 条/run）
- **软约束（prompt 级）**：工具描述写清楚"什么值得记"；返回当前记忆数量提示
- **后置清理（已有机制）**：`store_classified` 的 cosine dedup + Consolidation 的 decay/dedup/expire

**C. Agent 定义增加 `memory_enabled` 标志**

- `agents` 表新增 `memory_enabled` 布尔列（default false）
- 设为 true 的 Agent 自动获得 `memory_store` + `memory_recall` 工具
- CLI Agent（Claude/Codex）不注入此工具（CLI 自管理上下文）

**D. 与 agent-scoped-memory 的关系**

- `memory_store` 写入的 LTM 带 `scope='agent', agent_id=<当前 Agent>`
- 需 `add-agent-scoped-memory` 变更先行或同步实施
- 若 agent-scoped-memory 尚未实施，则写入 `scope='global'`（向后兼容）

## Capabilities

### New Capabilities

- `agent-memory-write`: Agent 主动写入长期记忆的能力，含三层防滥用机制

### Modified Capabilities

- `tools`: 新增 `memory_store` 工具定义

## Impact

- **后端代码**：新增 `backend/app/tools/memory_store.py`；修改 `backend/app/tools/registry.py`（注册）、`backend/app/services/agent_runner.py`（按 `memory_enabled` 注入工具）
- **数据库**：`agents` 表新增 `memory_enabled` 列（BOOLEAN, default false）
- **API**：无外部接口变更
- **前端**：Agent 编辑器后续可加 `memory_enabled` 开关（不在本变更范围）
- **依赖**：建议 `add-agent-scoped-memory` 先行；若无则降级为 global scope
- **风险**：Agent 可能写入低质量记忆——由三层防线缓解；限流可能误拒高价值写入——max 3 条/run 在正常使用下足够
