## Why

RAG 知识库检索当前是会话级开关：用户在消息输入框点 BookOpen 按钮切换 `Conversation.rag_enabled`，`agent_runner.py` 读这个标记后为会话内所有 custom agent 动态注入 4 个 RAG 工具。这与现有工具体系不一致——所有其他工具（`web_search`、`write_artifact` 等）都是 agent 级、通过 `agent.tool_names` 勾选的。会话级开关粒度太粗：群聊场景下所有 agent 都被迫拿到 RAG 工具，而实际上只有"调研员"角色真正需要知识库检索能力，"程序员"角色并不需要。

此外，`rag_ingest` / `rag_list_documents` / `rag_delete_document` 三个管理类工具对普通 agent 角色没有意义——文档上传和管理已经由知识库侧边栏（`KnowledgeSidebarNav`）和 guide agent 的 `manage_documents` 工具覆盖。普通 agent 只需要 `rag_search` 检索能力。

## What Changes

- **`rag_search` 加入 Agent 可选工具列表**：从会话级动态注入迁移为 agent 级 opt-in 工具，与 `web_search` / `write_artifact` 等保持一致。Agent 编辑器里多一个"知识库检索"复选框。
- **`researcher` 预设加上 `rag_search`**：调研员角色天然需要知识库检索，选预设时自动带上。
- **废弃 `Conversation.rag_enabled` 字段**：移除消息输入框的 RAG 开关按钮、`setRagMode` API 端点、`rag_enabled` 列（保留 DB 列不删，停止读写）。
- **移除 `agent_runner.py` 的会话级 RAG 注入逻辑**：`rag_search` 现在从 `agent.tool_names` 经 baseline merge 自然合并，不再需要 `conv.rag_enabled` 注入。
- **简化 prompt 指引**：RAG 指引段落只保留 `rag_search` 的说明，删除 `rag_ingest` / `rag_list_documents` / `rag_delete_document` 的指引。
- **`rag_ingest` / `rag_list_documents` / `rag_delete_document` 保留在 tool registry 但不再注入任何 agent**：留作休眠工具，文档管理由侧边栏 UI 和 guide agent 的 `manage_documents` 覆盖。
- **CLI agent 仍不支持 RAG**：`rag_search` 只对 custom（SDK）agent 生效，Claude Code / Codex 继续使用各自 CLI 内置工具集。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `tools`：`rag_search` 的触发方式从"会话级 `rag_enabled` 动态注入"改为"agent 级 `toolNames` opt-in"，与 `web_search` 等工具一致。其余 3 个 RAG 管理工具不再注入任何 agent。
- `agent-builder`：`AVAILABLE_AGENT_TOOLS` 从 5 个扩展到 6 个（新增 `rag_search`）；`researcher` 预设工具集加上 `rag_search`。
- `frontend`：移除消息输入框的 RAG 开关按钮及相关逻辑。
- `persistence`：`Conversation.rag_enabled` 列废弃（保留列不删，停止读写）。

## Impact

- **前端 `src/shared/agent-builder-config.ts`**：`AVAILABLE_AGENT_TOOLS` 加 `'rag_search'`；`AgentToolName` 类型更新；`AGENT_TOOL_META` 加 `rag_search` 元数据；`researcher` 预设 `tools` 加 `'rag_search'`。
- **前端 `src/components/message-input.tsx`**：删除 RAG 开关按钮 + `toggleRagMode` 函数 + `ragEnabled` 变量。
- **前端 `src/lib/api.ts`**：删除 `setRagMode` 函数。
- **前端 `src/db/schema.ts`**：移除 `ragEnabled` 字段。
- **后端 `backend/app/api/agents.py`**：`_AVAILABLE_AGENT_TOOLS` 加 `"rag_search"`；`_AGENT_TOOL_META` 加 `rag_search` 条目；`researcher` preset 的 `tools` 加 `"rag_search"`。
- **后端 `backend/app/services/agent_runner.py`**：删除 line 2053-2070 的 `conv.rag_enabled` → 注入 RAG_TOOLS 逻辑块；简化 line 3769-3794 的 RAG prompt 指引为只检测 `rag_search`。
- **后端 `backend/app/api/conversations.py`**：删除 `PATCH /conversations/{id}/rag-mode` 端点。
- **后端 `backend/app/schemas/requests.py`**：删除 `rag_enabled` 字段（两处）。
- **后端 `backend/app/db/models.py`**：`Conversation.rag_enabled` 列保留不删（向后兼容），停止读写。
- **不变**：`rag_search` 工具实现（`memory_rag.py`）、tool registry 注册（`registry.py`）、`RAGService`、`KnowledgeSidebarNav` 侧边栏、`manage_documents` guide 工具——全部不动。
- **兼容性**：已有 custom agent 的 `tool_names` 不含 `rag_search`，迁移后它们不再有 RAG 检索能力，需要用户在编辑 agent 时手动勾选。已有会话的 `rag_enabled=true` 标记将不再被读取。这是一次有意识的触发方式迁移。
