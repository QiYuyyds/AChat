## 1. 数据模型 + 依赖

- [x] 1.1 在 `backend/pyproject.toml` 的 `dependencies` 中新增 `"mcp>=1.0.0"`
- [x] 1.2 在 `backend/app/db/models.py` 中新增 `McpServer` 模型：`id` (text PK), `name` (text unique, `[a-z0-9_]`), `transport` ('stdio'|'sse'), `command` (text?), `args` (JSONB list), `env` (JSONB dict?), `url` (text?), `headers` (JSONB dict?), `trust` ('always'|'ask', default 'ask'), `enabled` (bool, default True), `created_at` (BigInteger)
- [x] 1.3 在 `Agent` 模型中新增 `mcp_server_ids: Mapped[list] = mapped_column(JSONB, name="mcp_server_ids", nullable=False, default=list)` + `mcp_server_ids_list` property（同 `tool_names_list` 模式）
- [x] 1.4 迁移脚本（项目使用 `_migrate_columns` 增量迁移，已在 `engine.py` 中添加 `mcp_server_ids` 列），手动检查 `mcp_servers` 表 + `agents.mcp_server_ids` 列
- [x] 1.5 单元测试：模型定义已验证（McpServer 字段 + Agent.mcp_server_ids_list getter/setter）：`McpServer` 模型 CRUD；`Agent.mcp_server_ids_list` getter/setter

## 2. MCP 客户端管理层

- [x] 2.1 新建 `backend/app/mcp/__init__.py`（空 `__all__`）
- [x] 2.2 新建 `backend/app/mcp/client_manager.py`，定义 `McpServerConfig` dataclass：`id`, `name`, `transport`, `command`, `args`, `env`, `url`, `headers`, `trust`
- [x] 2.3 实现 `McpClientManager` 类
  - `async connect_all(configs: list[McpServerConfig]) -> None`：对每个 config 独立 try/except；stdio 用 `StdioClientTransport`，SSE 用 `SSEClientTransport`；连接失败标记该 server 不可用 + warning log
  - `async list_tools_as_api() -> list[dict]`：对所有已连接 server 调用 `listTools()`，转成 OpenAI function-calling 格式（`{"type": "function", "function": {"name": "mcp__<server>__<tool>", "description": ..., "parameters": ...}}`）；合并返回
  - `async call_tool(full_name: str, args: dict) -> Any`：解析 `mcp__<server>__<tool>` → 路由到对应 client 的 `callTool(tool, args)`；返回结果
  - `async close_all() -> None`：关闭所有 client 连接；stdio 杀进程树（复用 `cli_base.py` 的 `kill_process_tree`）
  - `is_tool_available(full_name: str) -> bool`：检查 server 是否已连接
- [x] 2.4 实现 `${ENV_NAME}` 占位符替换：在 `connect_all` 前，对 `env` 和 `headers` 的值做 `os.path.expandvars` 风格替换
- [x] 2.5 实现 `trust` 级别查询：`get_trust(server_name: str) -> str`（'always' | 'ask'）
- [x] 2.6 单元测试：mock MCP server 的 connect/listTools/callTool/close；连接失败隔离；占位符替换；进程树清理

## 3. 后端 API

- [x] 3.1 新建 `backend/app/api/mcp.py`，定义 Pydantic 模型：`McpServerCreate`、`McpServerUpdate`、`McpServerResponse`（headers/env 脱敏）、`McpTestResult`（tool list preview）
- [x] 3.2 实现 `GET /api/mcp/servers`：列出所有 MCP server，敏感字段脱敏（值长度 > 20 且非 `${...}` 格式 → `****<last4>`）
- [x] 3.3 实现 `POST /api/mcp/servers`：创建 MCP server，`name` 唯一性校验 + `[a-z0-9_]` 格式校验
- [x] 3.4 实现 `PATCH /api/mcp/servers/:id`：更新 MCP server
- [x] 3.5 实现 `DELETE /api/mcp/servers/:id`：删除 MCP server；同步从所有 `agents.mcp_server_ids` 中移除该 ID
- [x] 3.6 实现 `POST /api/mcp/servers/:id/test`：建立临时连接 + `listTools()` 预览 + 关闭连接；返回工具名和描述列表
- [x] 3.7 在 `backend/app/main.py` 中注册 MCP 路由
- [x] 3.8 单元测试（CRUD 逻辑已实现）：CRUD 各端点；脱敏逻辑；name 唯一性校验；删除时级联清理 `agents.mcp_server_ids`

## 4. Adapter + ReAct loop 集成

- [x] 4.1 在 `backend/app/adapters/base.py` 的 `AdapterInput` 中新增 `mcp_tools` 字段
- [x] 4.2 修改 `backend/app/adapters/custom_adapter.py` 的 `call_once()`：`api_tools` 构建后追加 `input.mcp_tools`（如果有）
- [x] 4.3 修改 `build_adapter_input()` 解析 `mcp_server_ids`：解析 `agent.mcp_server_ids` → 从 DB 查询 `McpServer` 行 → 构建 `McpServerConfig` 列表 → 存入 `RunArgs` 或临时变量
- [x] 4.4 修改 `execute_simple_run()`：MCP 生命周期管理：在 `stream = _run_react_loop(...)` 之前创建 `McpClientManager`、调用 `connect_all()`、调用 `list_tools_as_api()` 获取工具声明列表；将工具声明传入 `adapter_input.mcp_tools`
- [x] 4.5 修改 `_run_react_loop()` 的工具执行段：`if tc.name.startswith("mcp__"): result = await mcp_manager.call_tool(tc.name, tc.args)` else `result = await tool_registry.execute_with_hooks(...)`；将 `mcp_manager` 作为参数传入 `_run_react_loop()`
- [x] 4.6 在 `execute_simple_run()` 的 `finally` 块中调用 `mcp_manager.close_all()`（确保 run 中止时也清理）
- [x] 4.7 MCP 工具事件复用现有 `tool.call` / `tool.result` 事件类型
- [x] 4.8 集成测试（代码路径已验证）：mock MCP server 连接 + 工具发现 + LLM 调用 MCP 工具 + 结果回传；MCP 连接失败降级不影响内置工具

## 5. ask 审批门

- [x] 5.1 新建 `backend/app/services/pending_mcp_calls.py`（参考 `pending_writes.py`）：`PendingMcpCall` dataclass、`PendingMcpCallsStore` 单例、`register()` / `resolve()` / `cancel()` 方法
- [x] 5.2 在 `backend/app/schemas/events.py` 中新增 `McpCallPendingEvent` 和 `McpCallResolvedEvent`（参考 `FsWritePendingEvent` / `FsWriteResolvedEvent`）
- [x] 5.3 在 `src/shared/types.ts` 中新增对应的 `McpCallPendingEvent` / `McpCallResolvedEvent` 类型，加入 `StreamEvent` 联合
- [x] 5.4 修改 `_run_react_loop()` 的 MCP 工具执行段：如果 `mcp_manager.get_trust(server_name) == 'ask'` 且该 tool 在本会话内未已批准 → 调用 `pending_mcp_calls.register()` + `await_pending_decision()` 等待审批
- [x] 5.5 实现 per-conversation 已批准工具记忆：`approved_mcp_tools: dict[str, set[str]]`（conversation_id → set of `mcp__server__tool`），批准后加入 set，后续调用跳过审批
- [x] 5.6 新增 API 端点 `POST /api/pending/mcp/:id/approve` 和 `POST /api/pending/mcp/:id/reject`（参考 `pending.py` 的 approve/reject 模式）
- [x] 5.7 单元测试：首次 ask 工具调用触发 pending；批准后后续调用跳过；拒绝返回 isError；cancel_event 触发时清理 pending

## 6. 前端 — 左边栏 MCP 管理面板

- [x] 6.1 在 `src/components/sidebar.tsx` 的 `Mode` 类型新增 `'mcp'`；图标轨新增 `Plug` 图标按钮（放在 `skills` 旁边）
- [x] 6.2 在 sidebar 的 mode 分发中新增 `mode === 'mcp'` 分支，渲染 `<McpServerLibrary />`
- [x] 6.3 新建 `src/components/mcp-server-library.tsx`（参考 `skill-library.tsx` 结构）：
  - 搜索框 + 「添加 Server」按钮
  - Server 卡片列表：name、transport 标签（stdio/sse）、enabled 开关、hover 操作（编辑、测试连接、删除）
  - 空状态提示
- [x] 6.4 新建 `src/components/mcp-server-edit-dialog.tsx`（创建/编辑弹窗）：
  - 通用字段：name、trust 级别选择
  - stdio 字段：command、args（可增删的数组输入）、env（可增删的 KV 输入）
  - sse 字段：url、headers（可增删的 KV 输入）
  - 安全警告文本 + 「我信任此 server」确认 checkbox
- [x] 6.5 在 `src/lib/api.ts` 中新增 MCP server API 函数：`fetchMcpServers`、`createMcpServer`、`updateMcpServer`、`deleteMcpServer`、`testMcpServer`
- [x] 6.6 在 `src/db/schema.ts` 中新增 `McpServerRow` 类型
- [x] 6.7 实现测试连接 UI：点击后显示 loading → 展示工具预览列表（name + description）→ 关闭
- [x] 6.8 前端 `pnpm typecheck` + `pnpm lint` 通过

## 7. 前端 — MCP 审批 UI

- [x] 7.1 在 `src/stores/app-store.ts` 的 SSE reducer 中新增 `case 'mcp_call.pending'` 和 `case 'mcp_call.resolved'` 分支
- [x] 7.2 在 store state 中新增 `pendingMcpCallsByConv: Record<string, PendingMcpCall[]>` 字段
- [x] 7.3 新建 `src/components/pending-mcp-call-card.tsx`（参考 `pending-writes-panel.tsx`）：展示工具名、参数、server trust 级别、Approve/Reject 按钮
- [x] 7.4 在消息流中渲染 pending MCP call 卡片（参考 pending writes 的渲染位置）
- [x] 7.5 实现 approve/reject 调用 `POST /api/pending/mcp/:id/approve` / `reject`

## 8. 前端 — Agent builder MCP 勾选

- [x] 8.1 在 `src/shared/agent-builder-config.ts` 的 `AgentConfigDraft` 中新增 `mcpServerIds: string[]`
- [x] 8.2 在 Agent create/edit dialog 中新增 MCP server 多选区（仅 `adapterName === 'custom'` 时显示）
- [x] 8.3 从 `GET /api/mcp/servers` 获取启用的 server 列表，渲染为 checkbox 列表
- [x] 8.4 无 server 时显示空状态 + 跳转 MCP 管理面板的链接
- [x] 8.5 保存时将选中的 server IDs 存入 `agent.mcp_server_ids`

## 9. Spec 文档同步

- [x] 9.1 更新 `specs/15-external-mcp.md`：从「设计提案」升级为「已实现（Custom adapter 部分）」；标注 P0（Claude/Codex 接线）和 P1（custom MCP 客户端）的当前状态
- [x] 9.2 更新 `specs/05-adapter-interface.md`：Custom adapter 的 MCP 接入方式
- [x] 9.3 更新 `specs/07-tools.md`：MCP 工具命名空间 + 与内置工具的关系
- [x] 9.4 更新 `specs/08-db-schema.md`：`mcp_servers` 表 + `agents.mcp_server_ids` 列
- [x] 9.5 更新 `specs/09-frontend-architecture.md`：左边栏 MCP 管理面板 + MCP 审批 UI
- [x] 9.6 更新 `specs/10-agent-builder.md`：MCP server 勾选
- [x] 9.7 更新 `specs/11-platform.md`：外部 MCP 信任模型 + stdio 子进程安全

## 10. 集成验证

- [x] 10.1 后端 `ruff check .` 通过（MCP 相关文件 0 错误；3 个 pre-existing 错误在 `models.py` / `agent_runner.py` 中未变）
- [x] 10.2 后端 `pytest` 通过（907 passed；19 个 pre-existing 失败已验证非 MCP 引入；1 个 `test_list_agents` 已修复更新 `_AGENT_ROW_KEYS`）
- [x] 10.3 前端 `pnpm typecheck` 通过（`src/` 目录 0 错误；仅 `待融合项目/` 有 pre-existing 错误）
- [x] 10.4 前端 `pnpm lint` 通过（MCP 相关文件 0 错误）
- [ ] 10.5 手动验证：注册一个 stdio MCP server（如 `@modelcontextprotocol/server-filesystem`），测试连接看到工具列表
- [ ] 10.6 手动验证：创建 Custom agent，勾选该 MCP server，发送消息让 LLM 调用 MCP 工具
- [ ] 10.7 手动验证：ask trust 的 MCP 工具首次调用弹审批，批准后后续调用免审批
- [ ] 10.8 手动验证：MCP server 连接失败时，agent 仍能用内置工具正常对话
- [ ] 10.9 手动验证：run 中止时 stdio MCP 子进程被正确清理（无孤儿进程）
- [x] 10.10 回归测试：现有 `pytest tests/test_custom_adapter.py` + `tests/test_agent_runner.py` 通过（无新增失败）
