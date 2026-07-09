# Proposal: Custom Agent 外部 MCP Client 接入

## Why

当前 Custom adapter（OpenAI 兼容 SDK 路线）的工具扩展完全靠手写内置工具（`backend/app/tools/`）。用户无法接入第三方 MCP server（如 filesystem / github / postgres / 自建工具），工具生态被封闭在 AChat 内部。

Spec 15 已设计了通用 MCP 接入方案，但决策（§11 决策 3）将 Custom adapter 的 MCP 客户端层推迟到 P1。本变更将其前置——让 Custom agent 作为 MCP client 连接用户配置的外部 MCP server，把外部工具注入到 ReAct loop 中。

## What Changes

- **新增 `mcp` Python 依赖**：官方 MCP Python SDK（`modelcontextprotocol/python-sdk`），用于 stdio / SSE 传输层的客户端连接。
- **新增 `mcp_servers` 数据表**：全局定义 MCP server（stdio / SSE），包含 command / args / env / url / headers / trust 级别。
- **`agents` 增列 `mcp_server_ids`**：per-agent 启用哪些 MCP server，语义同 `tool_names`。
- **新增 MCP 客户端管理层**（`backend/app/mcp/client_manager.py`）：per-run 生命周期管理——连接、工具发现、工具调用、清理。
- **改造 Custom adapter + ReAct loop**：`call_once()` 追加 MCP 工具声明；`_run_react_loop()` 工具执行路由 `mcp__` 前缀到 MCP client。
- **`ask` trust 审批门**：trust 级别为 `ask` 的 server，其工具首次在某会话内调用时走 pending 审批（复用 `await_pending_decision` 机制），批准后该会话内该工具免再问。
- **新增后端 API**（`backend/app/api/mcp.py`）：MCP server CRUD + 测试连接。
- **前端左边栏新增「MCP」入口**：`McpServerLibrary` 组件，对齐现有 `SkillLibrary` / `AgentLibrary` 模式。
- **Agent builder 新增 MCP server 勾选**：仅对 Custom adapter 显示。

## Capabilities

### New Capabilities

- `external-mcp`: Custom agent 作为 MCP client 连接外部 MCP server，进行工具发现、调用和生命周期管理

### Modified Capabilities

- `adapters`: Custom adapter 的 `call_once()` 追加 MCP 工具声明；ReAct loop 工具执行路由 `mcp__` 前缀
- `tools`: MCP 工具命名空间 `mcp__<serverName>__<toolName>`；与内置工具的关系
- `persistence`: 新增 `mcp_servers` 表；`agents` 表增列 `mcp_server_ids`
- `agent-builder`: Agent 创建/编辑表单新增 MCP server 多选（仅 Custom adapter）
- `frontend`: 左边栏新增 MCP server 管理面板；Agent builder MCP 勾选
- `platform-security`: 外部 MCP 信任模型；`ask` 审批门；stdio 子进程安全

## Impact

- **新增依赖**：`mcp`（官方 Python SDK，`pip install mcp`）
- **新增文件**：
  - `backend/app/mcp/client_manager.py`（MCP 客户端管理层）
  - `backend/app/mcp/__init__.py`
  - `backend/app/api/mcp.py`（MCP server CRUD API）
  - `src/components/mcp-server-library.tsx`（左边栏 MCP 管理面板）
  - `src/components/mcp-server-edit-dialog.tsx`（编辑/创建弹窗）
- **修改文件**：
  - `backend/app/db/models.py`（新增 `McpServer` 模型 + `Agent.mcp_server_ids`）
  - `backend/app/adapters/custom_adapter.py`（`call_once()` 追加 MCP 工具声明）
  - `backend/app/adapters/base.py`（`AdapterInput` 增加 MCP 相关字段）
  - `backend/app/services/agent_runner.py`（`_run_react_loop()` 工具执行路由 + `build_adapter_input()` MCP 配置解析 + `execute_simple_run()` 生命周期管理）
  - `backend/app/main.py`（路由注册）
  - `backend/pyproject.toml`（新增 `mcp` 依赖）
  - `src/components/sidebar.tsx`（新增 `mcp` mode + 图标轨入口）
  - `src/shared/agent-builder-config.ts`（MCP server 勾选配置）
  - `src/lib/api.ts`（MCP server API 函数）
  - `src/db/schema.ts`（McpServerRow 类型）
- **Spec 文档**：`specs/15-external-mcp.md` 从设计提案升级为已实现；`specs/05-adapter-interface.md` / `specs/07-tools.md` / `specs/08-db-schema.md` / `specs/09-frontend-architecture.md` / `specs/10-agent-builder.md` / `specs/11-platform.md` 同步更新
- **安全**：外部 MCP server 运行任意代码（stdio）或访问外部网络（SSE），绕过 AChat 的 workspace 沙箱 + Bash 黑名单。通过显式 opt-in（server 登记 + agent 勾选）+ `ask` 审批门缓解。
- **平台兼容**：stdio MCP server 的子进程管理复用现有 `cli_base.py` 的进程清理逻辑（spec 11）；Windows 上需确保子进程窗口隐藏
