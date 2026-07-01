## Why

当前 AgentHub 的 Claude Code 和 Codex 集成基于 SDK 直调 API（anthropic SDK / codex-sdk），与 Multica 的 CLI spawn 模式不一致。SDK 方式需要自行实现 tool loop、工具调度、上下文管理，且无法复用 CLI 已有的 sandbox、session、MCP 等基础设施。需要改为像 Multica 一样通过 spawn codex CLI 二进制实现 agent 集成，让 codex 自带的工具链、sandbox 安全模型和 session 管理开箱即用。

## What Changes

- **BREAKING** 删除 `ClaudeAdapter`（`backend/app/adapters/claude_adapter.py`）—— 基于 anthropic Python SDK 直调 Messages API 的实现，包括自写 tool loop（MAX_TURNS=8）和四层 Key 解析中 claude-code 专有分支
- **BREAKING** 从 `AdapterName` 类型中移除 `"claude-code"` 值，后端 `base.py` 和前端 `types.ts` 同步修改
- **BREAKING** 前端 agent builder 不再提供 claude-code adapter 选项，已有 claude-code 类型的 agent 将无法运行（需迁移为 codex 或 custom）
- 新增 `CodexCLIAdapter`（`backend/app/adapters/codex_cli_adapter.py`）—— 基于 `asyncio.create_subprocess_exec` spawn `codex app-server --listen stdio://`，通过 JSON-RPC 2.0 over stdin/stdout 通信
- 新增 `codex_home.py` 模块 —— 管理 per-task `CODEX_HOME` 目录（symlink auth.json、copy config.toml、注入 `[mcp_servers.agenthub]` 块），参考 Multica 的 `execenv/codex_home.go`
- 复用已有 `scripts/agenthub-codex-mcp.mjs` 作为 MCP bridge，将 AgentHub 内部工具（write_artifact、report_task_result 等）通过 stdio MCP 协议暴露给 codex CLI
- 修改 `build_adapter_input` 移除 claude-code 专有的 Key 解析和 base_url fallback 分支
- 修改 `registry.py` 移除 ClaudeAdapter 注册，新增 CodexCLIAdapter 注册
- 保留 `CustomAdapter`（openai SDK）和 `MockAdapter` 不变，Orchestrator 仍以 CustomAdapter 为运行载体

## Capabilities

### New Capabilities
- `codex-cli-adapter`: Codex CLI spawn 方式的 adapter 实现，包括 subprocess 生命周期管理、JSON-RPC 2.0 通信协议、事件翻译为 StreamEvent、per-task CODEX_HOME 隔离、MCP 配置注入

### Modified Capabilities
- `adapters`: 移除 ClaudeCodeAdapter 的 requirements（SDK 直调、自写 tool loop、四层 Key 解析 claude-code 分支），新增 CodexCLIAdapter 的 requirements（CLI spawn、JSON-RPC、MCP 注入、CODEX_HOME 隔离）
- `agent-builder`: adapter 选项矩阵变更——移除 claude-code，codex 的配置逻辑从 SDK 模式改为 CLI 模式（无需 api_key/api_base_url，可选 executable_path 覆盖）
- `frontend`: agent 创建对话框适配——移除 claude-code 选项，codex 选项的表单字段变更（去掉 api_key/base_url，增加可选的 executable_path 和 model 覆盖）

## Impact

- **后端 adapter 层**: 删除 `claude_adapter.py`（390行），新建 `codex_cli_adapter.py` + `codex_home.py`，修改 `registry.py` / `base.py` / `agent_runner.py` 的 `build_adapter_input` 和 `_pick_settings_key`
- **前端**: `src/shared/types.ts` 的 AdapterName 类型、`src/shared/agent-builder-config.ts` 的默认模型配置、`src/shared/codex-compat.ts` 的 base URL 验证、`src/components/create-agent-dialog.tsx` 的 adapter 选择 UI
- **MCP bridge**: `scripts/agenthub-codex-mcp.mjs` 无需修改，直接复用
- **数据库**: agents 表的 `api_key` / `api_base_url` 字段保留（Custom adapter 仍需要），codex 类型 agent 不再使用这些字段
- **Spec 文档**: `specs/05-adapter-interface.md`、`specs/08-db-schema.md`、`specs/10-agent-builder.md` 需同步更新
- **依赖**: 不再需要 `anthropic` Python SDK（可从 requirements.txt 移除），不需要 `@openai/codex-sdk`
- **运行前提**: 用户机器上需安装 codex CLI（`npm install -g @openai/codex` 或 Store 版应用的 CLI 组件），并在 PATH 上可用
