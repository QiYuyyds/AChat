## 1. 删除旧 Claude SDK 实现

- [x] 1.1 删除 `backend/app/adapters/claude_adapter.py`（390行，anthropic SDK + tool loop）
- [x] 1.2 从 `backend/app/adapters/registry.py` 移除 `ClaudeAdapter` 注册和 import
- [x] 1.3 从 `backend/app/adapters/base.py` 的 `AdapterName` Literal 中移除 `"claude-code"`
- [x] 1.4 从 `backend/app/services/agent_runner.py` 的 `build_adapter_input` 中移除 claude-code 专有分支（`api_base_url` fallback 到 `settings.anthropic_base_url`、`prefix_prompt_with_context_summary` 中的 `"claude-code"` 检查）
- [x] 1.5 从 `backend/app/services/agent_runner.py` 的 `_pick_settings_key` 中移除 `claude-code` 分支
- [x] 1.6 从 `backend/requirements.txt` 中移除 `anthropic` 依赖（确认无其他模块使用后）

## 2. 新建 codex_home.py — CODEX_HOME 管理模块

- [x] 2.1 创建 `backend/app/adapters/codex_home.py`，实现 `prepare_codex_home(run_id, data_dir, mcp_env)` 函数
- [x] 2.2 实现共享目录 symlink 逻辑：`auth.json` 和 `sessions/` 从 `~/.codex/` symlink 到 per-task 目录
- [x] 2.3 实现配置文件 copy 逻辑：`config.toml` 从 `~/.codex/config.toml` copy 到 per-task 目录（如源文件不存在则创建空文件）
- [x] 2.4 实现 MCP 配置注入：在 `config.toml` 中写入 `[mcp_servers.agenthub]` TOML 块（BEGIN/END 标记包裹，0o600 权限）
- [x] 2.5 实现 `cleanup_codex_home(codex_home_dir)` 清理函数（run 结束后可选清理）
- [x] 2.6 编写单元测试 `backend/tests/test_codex_home.py`：验证 symlink 创建、config.toml 注入、权限设置

## 3. 新建 codex_cli_adapter.py — 核心 adapter

- [x] 3.1 创建 `backend/app/adapters/codex_cli_adapter.py`，定义 `CodexCLIAdapter` 类实现 `AgentPlatformAdapter` 接口
- [x] 3.2 实现 `name` 属性返回 `"codex"`
- [x] 3.3 实现 `_resolve_executable(input)` 方法：`executable_path` → `CODEX_EXECUTABLE` env → PATH 搜索 `codex`
- [x] 3.4 实现 `stream()` 方法骨架：调用 `prepare_codex_home` → `asyncio.create_subprocess_exec` → 读写 stdin/stdout
- [x] 3.5 实现 JSON-RPC 2.0 客户端：`_send_request(method, params)` 写 stdin，`_read_events()` 逐行读 stdout
- [x] 3.6 实现 `thread/start` + `thread/run` 请求发送：包含 prompt、system_prompt、instructions
- [x] 3.7 实现事件翻译器 `_translate_event(rpc_event) -> list[StreamEvent]`：处理 `thread.started`、`agent_message`、`reasoning`、`command_execution`、`mcp_tool_call`、`turn.completed`
- [x] 3.8 实现 `MessageStartEvent` / `PartStartEvent` / `PartDeltaEvent` / `MessageEndEvent` 的正确产出顺序
- [x] 3.9 实现 `ToolCallEvent` + `ToolResultEvent` 翻译（codex 命令执行和 MCP 工具调用）
- [x] 3.10 实现 `RunUsageEvent` 翻译（从 `turn.completed` 提取 token 用量）
- [x] 3.11 实现取消逻辑：`cancel_event` 监听 → `proc.terminate()` → 5s 超时 → `proc.kill()`
- [x] 3.12 实现 `finally` 块清理：关闭 stdin、等待进程退出、清理残留 MCP bridge 进程
- [x] 3.13 实现错误处理：codex 找不到、spawn 失败、JSON-RPC 错误、stdout 解析失败 → 产出错误 `MessageEndEvent`

## 4. 修改 adapter 注册和接口

- [x] 4.1 在 `backend/app/adapters/registry.py` 中注册 `CodexCLIAdapter`
- [x] 4.2 确认 `backend/app/adapters/base.py` 的 `AdapterName` 改为 `Literal["mock", "custom", "codex"]`
- [x] 4.3 在 `AdapterInput` 中新增 `executable_path: str | None = None` 字段（可选的 codex 二进制路径）
- [x] 4.4 在 `backend/app/db/models.py` 的 Agent 模型中新增 `executable_path` 列（可选，String，nullable）
- [x] 4.5 在 `backend/app/schemas/agents.py` 的 AgentPydantic 中新增 `executable_path` 字段

## 5. 修改 AgentRunner

- [x] 5.1 在 `build_adapter_input` 中为 codex adapter 设置 `executable_path`（从 agent 字段读取）
- [x] 5.2 在 `build_adapter_input` 中移除 codex adapter 的 `api_key` / `api_base_url` 传递（codex 不需要）
- [x] 5.3 修改 `_pick_settings_key`：移除 codex 分支（codex CLI 自己管理认证）
- [x] 5.4 在 `build_adapter_input` 中保留 codex 的 `prefix_prompt_with_context_summary` 调用（将 `"claude-code"` 检查改为只检查 `"codex"`）
- [x] 5.5 确认 `execute_simple_run` 中 codex adapter 不注入 `memory_recall`、`load_skill`、RAG 工具（这些是 custom 专有的；codex 通过 MCP 获取工具）

## 6. 前端适配

- [x] 6.1 修改 `src/shared/types.ts`：`AdapterName` 从 `'claude-code' | 'codex' | 'custom' | 'mock'` 改为 `'codex' | 'custom' | 'mock'`
- [x] 6.2 修改 `src/shared/agent-builder-config.ts`：移除 `CLAUDE_CODE_DEFAULT_MODEL`，保留 `CODEX_DEFAULT_MODEL`
- [x] 6.3 删除 `src/shared/codex-compat.ts`（Codex base URL 验证不再需要）
- [x] 6.4 修改 `src/components/create-agent-dialog.tsx`：移除 `claude-code` adapter 选项，codex 选项的表单字段改为 `executable_path` + `model`（可选）
- [x] 6.5 修改 `src/db/schema.ts`：在 agents 表中新增 `executablePath` 列
- [x] 6.6 修改 `src/shared/types.ts` 的 Agent 接口：新增 `executablePath?: string`
- [x] 6.7 移除前端中所有对 `claude-code` adapter 的引用和条件渲染

## 7. 后端 API 和数据层

- [x] 7.1 修改 `backend/app/api/agents.py`：在 create/update agent API 中接受 `executable_path` 字段
- [x] 7.2 修改 `backend/app/schemas/agents.py`：在 AgentCreate/AgentUpdate 中新增 `executable_path`
- [x] 7.3 确认 agents 表的 `api_key` / `api_base_url` 列保留（Custom adapter 仍需要）
- [x] 7.4 在 agent API 的 validation 中：codex adapter 不再要求 `api_key` / `api_base_url`

## 8. MCP bridge 环境变量对接

- [x] 8.1 确认 `scripts/agenthub-codex-mcp.mjs` 的 `AGENTHUB_ALLOWED_TOOLS` 解析逻辑正确
- [x] 8.2 在 `CodexCLIAdapter` 中构造 MCP env：`AGENTHUB_INTERNAL_BASE_URL`、`AGENTHUB_INTERNAL_TOOL_TOKEN`、`AGENTHUB_CONVERSATION_ID`、`AGENTHUB_AGENT_ID`、`AGENTHUB_RUN_ID`、`AGENTHUB_ALLOWED_TOOLS`（从 `tool_names` 拼接）
- [x] 8.3 确认后端 `/api/internal/agenthub-tools` 端点接受并验证 `AGENTHUB_INTERNAL_TOOL_TOKEN`
- [x] 8.4 确认 `report_task_result` 工具在 MCP bridge 中已注册且可通过 `AGENTHUB_ALLOWED_TOOLS` 启用

## 9. 集成测试

- [x] 9.1 编写 `backend/tests/test_codex_cli_adapter.py`：mock subprocess，验证 JSON-RPC 请求发送和事件翻译逻辑
- [x] 9.2 编写端到端测试：创建 codex agent → 创建对话 → 发消息 → 验证 SSE 事件流（需 codex CLI 可用，标记为 integration test 可跳过）
- [x] 9.3 编写群聊测试：Orchestrator agent + codex child agent → 验证 dispatch 流程和 `report_task_result` 解析
- [x] 9.4 验证取消逻辑：启动 codex run → 触发 cancel → 验证子进程被 terminate
- [x] 9.5 验证 CODEX_HOME 隔离：两个并发 codex run → 各自的 CODEX_HOME 目录独立、auth.json symlink 共享

## 10. 文档和清理

- [x] 10.1 更新 `specs/05-adapter-interface.md`：移除 ClaudeCodeAdapter 章节，新增 CodexCLIAdapter 章节
- [x] 10.2 更新 `specs/08-db-schema.md`：agents 表新增 `executable_path` 列说明，codex adapter 不再使用 `api_key`/`api_base_url`
- [x] 10.3 更新 `specs/10-agent-builder.md`：adapter 选项矩阵变更
- [x] 10.4 在 `CLAUDE.md` 或 `QUICKSTART.md` 中新增 codex CLI 安装说明（`npm install -g @openai/codex`）
- [x] 10.5 检查并清理代码库中所有对 `claude-code` adapter 的残留引用（grep `claude-code` / `claudeCode` / `ClaudeAdapter`）
