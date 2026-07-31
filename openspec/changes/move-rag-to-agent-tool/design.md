## Context

AChat 工具系统分两层：9 个 baseline 工具（`read_attachment` / `ask_user` / `fs_*` / `bash`）始终合并到 custom agent，5 个 UI 可选工具（`write_artifact` / `deploy_artifact` / `deploy_workspace` / `read_artifact` / `web_search`）通过 `agent.tool_names` 勾选。运行时 `agent_runner.py` 做 `effective_tools = BASELINE + agent.tool_names + 自动注入`，然后 `tool_registry.resolve(names)` 查到 `ToolDef` 后交给 adapter。

`rag_search` 工具本身已注册在 `tool_registry`（`registry.py:202`），`ToolDef` 在 `memory_rag.py` 定义完毕。问题不在工具实现，而在**触发路径**：当前走的是会话级 `conv.rag_enabled` → `agent_runner.py:2053` 动态注入 4 个 RAG 工具（仅 SDK agent），而不是像其他可选工具那样从 `agent.tool_names` 自然合并。

`researcher` 预设在 `agent-builder-config.ts` 的 `AGENT_TOOL_PRESETS` 里定义，当前 tools 是 `['write_artifact', 'read_artifact', 'web_search']`。

前端 RAG 开关在 `message-input.tsx:1124` 是一个 `BookOpen` 图标按钮，调 `setRagMode()` → `PATCH /conversations/{id}/rag-mode`。知识库侧边栏（`KnowledgeSidebarNav`）是独立的 sidebar mode，做文档上传/Obsidian 同步/浏览，与 RAG 工具触发无关，不受影响。

## Goals / Non-Goals

**Goals:**
- `rag_search` 成为 agent 级可选工具，走和 `web_search` 完全相同的路径：`agent.tool_names` → baseline merge → `tool_registry.resolve`。
- `researcher` 预设自动带 `rag_search`。
- 移除会话级 RAG 触发的全部前端和后端代码路径。
- CLI agent 不受影响（它们本来就不参与 baseline merge，`tool_names` 为空）。

**Non-Goals:**
- 不让 `rag_ingest` / `rag_list_documents` / `rag_delete_document` 成为 agent 可选工具——文档管理走侧边栏 UI 和 guide agent。
- 不删除 `rag_ingest` / `rag_list_documents` / `rag_delete_document` 的 `ToolDef` 注册——留作休眠，未来可复用或清理。
- 不删除 DB 里的 `rag_enabled` 列——保留列不删，只停止读写，避免 migration 风险。
- 不让 CLI agent 支持 RAG。
- 不改 `RAGService` 检索后端（Milvus / ES / Neo4j 混合检索）。

## Decisions

### D1 — `rag_search` 走 Strategy B（register + opt-in via toolNames），不自动注入

`rag_search` 已在 `tool_registry` 注册。把它加入 `AVAILABLE_AGENT_TOOLS`（前后端同步），agent 编辑器多一个复选框。运行时 baseline merge 自然包含它。**删除 `agent_runner.py:2053-2070` 的 `conv.rag_enabled` 注入逻辑块**。
- *Why:* 与 `web_search` 路径完全一致，零 `agent_runner` 特殊逻辑。agent 级粒度——只有需要的 agent 才有 RAG。
- *Alternatives:* 保留 `conv.rag_enabled` 作为会话级覆盖（双向机制）——拒绝：两套触发路径同时存在会让 prompt 指引和调试变复杂，违背"工具归 agent"的一致性原则。

### D2 — `researcher` 预设加 `rag_search`

`AGENT_TOOL_PRESETS` 里 `researcher` 的 `tools` 从 `['write_artifact', 'read_artifact', 'web_search']` 改为 `['write_artifact', 'read_artifact', 'web_search', 'rag_search']`。后端 `_AGENT_TOOL_PRESETS` 镜像同步。
- *Why:* 调研员天然需要知识库检索——用户选"调研员"预设时自动带 RAG 能力，不用手动再勾。
- *Trade-off:* 知识库可能没内容时 `rag_search` 会空转，但 `rag_search` handler 已有错误处理（返回 `"RAG service not initialized"` 或空结果），不会崩。

### D3 — `rag_enabled` 列保留不删，停止读写

`Conversation.rag_enabled` 列保留在 `models.py` 里，但：
- 前端删除 RAG 开关按钮和 `setRagMode` API 调用
- 后端删除 `PATCH /conversations/{id}/rag-mode` 端点
- 后端删除 `requests.py` 里的 `rag_enabled` 字段
- `agent_runner.py` 不再读 `conv.rag_enabled`
- `schema.ts` 移除 `ragEnabled` 字段
- *Why:* 避免 DB migration 风险（删列需要 `ALTER TABLE DROP COLUMN`），列留着不影响任何逻辑。已有的 `true` 值变成死数据，不会被读取。

### D4 — 简化 prompt 指引

`agent_runner.py:3769-3794` 当前检测 4 个 RAG 工具并分别生成指引段落。改为只检测 `rag_search`：
- `has_rag = "rag_search" in tools`
- 保留 `rag_search` 的使用说明
- 删除 `rag_ingest` / `rag_list_documents` / `rag_delete_document` 的指引段落
- 使用建议改为只提检索，不提入库（入库由侧边栏 UI 负责）
- *Why:* 只有 `rag_search` 会出现在 agent 工具列表里，指引其他 3 个工具是误导。

### D5 — `rag_ingest` / `rag_list_documents` / `rag_delete_document` 留在 registry 休眠

这 3 个工具的 `ToolDef` 和 handler 保留在 `memory_rag.py`，`registry.py` 里的 `reg.register(...)` 不动。它们仍可被 `tool_registry.get()` 查到，但不会出现在任何 agent 的 `tool_names` 里（因为不在 `AVAILABLE_AGENT_TOOLS` 列表中），也不会被自动注入。
- *Why:* 删除它们需要改 `registry.py` + `memory_rag.py`，且 `manage_documents` guide 工具已经覆盖文档管理需求。留着不碍事，未来如果要给特定 agent 管理文档能力可以复用。

## Risks / Trade-offs

- **已有 agent 迁移**：现有 custom agent 的 `tool_names` 不含 `rag_search`，迁移后它们失去 RAG 检索能力。这是有意识的触发方式迁移——用户需要重新编辑 agent 勾选 `rag_search`。在 release notes / change log 里说明。
- **已有会话的 `rag_enabled=true` 变死数据**：这些会话里的 agent 如果 `tool_names` 不含 `rag_search`，就不再有 RAG 能力。与上一条同理。
- **`researcher` 预设变化影响新 agent**：用户选 researcher 预设时会自动多一个工具，agent 的 `tool_names` 会比之前多 `rag_search`。这是预期行为。
- **`rag_ingest` 等休眠工具的维护成本**：它们留在 registry 里但不再被使用，代码可能随时间腐化。低风险——handler 逻辑简单且独立。
