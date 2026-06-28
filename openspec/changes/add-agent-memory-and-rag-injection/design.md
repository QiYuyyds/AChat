## Context

AChat 后端已在 `tool_registry` 注册了 `memory_recall` 和 4 个 `rag_*` 工具（`rag_search`、`rag_ingest`、`rag_list_documents`、`rag_delete_document`），但前端 `AVAILABLE_AGENT_TOOLS` 白名单未包含它们。Agent 的 `tool_names` 字段由前端表单决定，因此这些工具从未被任何 Agent 使用过。

当前 `agent_runner.py` 的工具解析逻辑：`base_tool_names = args.override_tool_names or agent.tool_names_list` → `tool_registry.resolve(tool_names)`。没有任何隐式注入或动态注入机制。

`conversation_service.py` 已有 `fs_write_approval_mode` 会话级开关的完整模式（DB 字段 → API endpoint → 前端 toggle 按钮），可复用。

## Goals / Non-Goals

**Goals:**
- 所有 custom agent 自动具备 `memory_recall` 基础记忆能力
- 用户可在对话界面通过开关启用 RAG 全量能力（4 个工具）
- 单聊和群聊均支持 RAG 开关
- 向后兼容，不破坏已有会话和 Agent 配置

**Non-Goals:**
- 不在前端 `AVAILABLE_AGENT_TOOLS` 中暴露 `memory_recall` 或 `rag_*` 工具选项
- 不改变 `claude-code` / `codex` adapter 的工具集（它们用 SDK 内置工具）
- 不实现 RAG 工具的审批/沙箱约束（后续可加）
- 不改变 Orchestrator 的 plan/execute/aggregate 工作流

## Decisions

### Decision 1: memory_recall 用路径 B（后端隐式注入）而非路径 A（前端默认勾选）

**选择**：在 `agent_runner.py` 的 tool resolve 前自动追加 `memory_recall`。

**不选择**：在 `AVAILABLE_AGENT_TOOLS` 和 `DEFAULT_CUSTOM_AGENT_TOOLS` 中加入 `memory_recall`。

**理由**：
- 记忆是 Agent 基础能力，不应被用户误删
- 路径 A 依赖用户不取消勾选，无法保证所有 Agent 都有记忆
- 路径 B 一行代码 + 日志，零 UI 复杂度
- 未来如果要在前端展示，可以加"基础能力"只读标签，不影响后端逻辑

### Decision 2: RAG 用会话级持久开关而非 Agent 级属性

**选择**：`conversation.rag_enabled` 字段 + `PATCH /api/conversations/{id}/rag-mode`。

**不选择**：在 Agent 创建表单中加入 RAG 工具选项。

**理由**：
- RAG 是"这个对话需要知识库"的语义，不是"这个 Agent 有能力"的语义
- 会话级开关更灵活：同一个 Agent 在不同会话中可以有/没有 RAG
- 复用 `fsWriteApprovalMode` 的完整模式（DB + API + 前端 toggle），实现成本最低
- 用户不需要编辑 Agent 配置就能启用 RAG

### Decision 3: RAG 开关启用时全量注入 4 个工具

**选择**：启用 RAG 时注入 `rag_search` + `rag_ingest` + `rag_list_documents` + `rag_delete_document`。

**不选择**：只注入 `rag_search`（只读检索）。

**理由**：
- Agent 需要完整知识库管理能力（入库、列表、删除）
- 减少后续"为什么 Agent 不能往知识库写东西"的困惑
- 可通过 RAG 开关统一控制，不需要细粒度权限

### Decision 4: 注入点在 conversation_service 而非 agent_runner

**选择**：在 `conversation_service.send_message()` 中，根据 `conv.rag_enabled` 为每个 responder 的 `tool_names` 追加 RAG 工具，然后传给 `agent_runner`。

**不选择**：在 `agent_runner` 中读取 conversation 的 `rag_enabled`。

**理由**：
- `agent_runner` 不应该关心 conversation 级状态
- `conversation_service` 已经是 orchestrator 分派逻辑的拥有者
- 子 Agent 通过 `override_tool_names` 也能继承 RAG 工具

## Risks / Trade-offs

- **[RAG 写操作无审批]** → `rag_ingest`/`rag_delete_document` 有副作用但无审批。缓解：RAG 开关本身是显式启用，用户已知晓；后续可加审批。
- **[memory_recall 隐式注入不可见]** → 用户不知道 Agent 有记忆能力。缓解：日志记录；未来可在 Agent 详情页显示"基础能力"标签。
- **[RAG 开关粒度]** → 会话级开关无法精确到单条消息。缓解：这是有意的设计取舍，与 `fsWriteApprovalMode` 保持一致。
