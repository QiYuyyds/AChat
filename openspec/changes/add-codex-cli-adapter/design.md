## Context

AgentHub 当前的 agent 集成有三种 adapter：

1. **ClaudeAdapter** — 用 `anthropic` Python SDK 直调 Messages API，自写 tool loop（MAX_TURNS=8），自行处理 tool_use/tool_result 事件，四层 Key 解析链（agent.api_key → app_settings → process.env → ~/.claude/.credentials.json OAuth fallback）
2. **CustomAdapter** — 用 `openai` Python SDK 调 OpenAI 兼容 API，支持 function calling，Orchestrator 以此为运行载体
3. **MockAdapter** — 测试用的脚本化假回复

Multica 的做法完全不同：统一 `Backend` 接口，每个 provider spawn 对应 CLI 二进制（`codex app-server --listen stdio://`），通过 stdin/stdout JSON-RPC 2.0 通信，复用 CLI 自带的 sandbox、session、MCP 等基础设施。

本次变更将 AgentHub 的 codex 集成从 SDK 模式改为 CLI spawn 模式，同时删除 Claude Code 的 SDK 集成。Custom adapter 保留不变。

## Goals / Non-Goals

**Goals:**
- 实现 `CodexCLIAdapter`，通过 spawn `codex app-server --listen stdio://` 与 codex CLI 通信
- 复用已有 `scripts/agenthub-codex-mcp.mjs` MCP bridge，将 AgentHub 内部工具暴露给 codex
- per-task `CODEX_HOME` 隔离，symlink 共享 auth.json，copy 隔离 config.toml
- codex 事件流翻译为 AgentHub 的 `StreamEvent`，支持流式输出
- 天然支持单聊（用户直接对话）和群聊（Orchestrator 调度子任务）
- 删除 ClaudeAdapter 的全部代码和 spec

**Non-Goals:**
- 不实现 Claude Code 的 CLI spawn adapter（用户电脑上目前只有 codex）
- 不修改 `AgentRunner`、`Orchestrator`、`consume_stream`、`event_bus`、SSE 等核心流程
- 不修改 `agenthub-codex-mcp.mjs` 的工具定义
- 不实现 codex session 续接（每次 run 创建新 session，后续可扩展）
- 不实现审批桥（codex 内置 bash/文件操作走 sandbox，不走 AgentHub pending writes）
- 不修改 Custom adapter 和 Mock adapter

## Decisions

### D1: 通信协议 — `codex app-server --listen stdio://` + JSON-RPC 2.0

**选择**: 按 Multica 的实现，spawn `codex app-server --listen stdio://`，通过 JSON-RPC 2.0 over stdin/stdout 通信。

**理由**: Multica 已验证此方案可行。`app-server` 子命令提供长连接的 JSON-RPC 服务，支持 `thread/start` → `thread/run` 生命周期，能接收流式事件输出。替代方案 `codex exec --json` 是单次执行模式，不支持 MCP server 注入和 session 续接。

**替代方案**: `codex exec --json` 单次模式。不考虑——不支持 MCP 注入，每次调用无状态。

### D2: MCP 工具注入 — per-task config.toml 中的 `[mcp_servers.agenthub]` 块

**选择**: 在 per-task 的 `$CODEX_HOME/config.toml` 中写入 `[mcp_servers.agenthub]` TOML 块，配置 `command="node"`、`args=["scripts/agenthub-codex-mcp.mjs"]`、`env={AGENTHUB_*}`。

**理由**: Codex CLI 从 `config.toml` 读取 MCP server 配置，启动时自动 spawn MCP server 子进程并完成握手。这比通过 `-c mcp_servers.agenthub=...` 命令行参数更稳定（避免 argv 长度限制和 secret 泄露风险）。Multica 也采用此方案（`ensureCodexMcpConfig` 函数）。

**环境变量传递**: MCP bridge 需要以下环境变量：
- `AGENTHUB_INTERNAL_BASE_URL` — 后端 API 地址
- `AGENTHUB_INTERNAL_TOOL_TOKEN` — 认证 token
- `AGENTHUB_CONVERSATION_ID` / `AGENTHUB_AGENT_ID` / `AGENTHUB_RUN_ID` — 运行上下文
- `AGENTHUB_ALLOWED_TOOLS` — 允许的工具列表（逗号分隔，对应 `tool_names`）

### D3: CODEX_HOME 隔离策略

**选择**: 每次 run 创建独立的 `<dataDir>/codex-home/<run_id>/` 目录。

- `auth.json` → symlink `~/.codex/auth.json`（共享认证状态，token 刷新自动生效）
- `sessions/` → symlink `~/.codex/sessions/`（共享日志，用户可查）
- `config.toml` → copy `~/.codex/config.toml` + 注入 MCP 块（隔离配置，防止用户全局配置干扰）
- `config.toml` 文件权限 0o600（MCP env 可能携带 secret）

**理由**: 参考 Multica 的 `execenv/codex_home.go`。symlink auth 保证 token 刷新不需要重启；copy config 防止用户全局 `mcp_servers` 与 managed 块冲突（TOML 不允许重复表定义）。

### D4: 事件翻译映射

**选择**: 将 codex JSON-RPC 事件翻译为 AgentHub `StreamEvent`：

| codex 事件 | StreamEvent | 说明 |
|---|---|---|
| `thread.started` | (内部缓存 thread_id) | 不产出前端事件 |
| `agent_message` (delta) | `PartStartEvent(text)` + `PartDeltaEvent` | 首次 delta 产出 PartStart，后续产出 PartDelta |
| `reasoning` (delta) | `PartStartEvent(thinking)` + `PartDeltaEvent` | 同上，thinking part |
| `command_execution` | `ToolCallEvent` + `ToolResultEvent` | codex 内置 bash/文件操作 |
| `mcp_tool_call` | `ToolCallEvent` + `ToolResultEvent` | AgentHub MCP 工具（write_artifact 等） |
| `turn.completed` | `MessageEndEvent` + `RunUsageEvent` | 结束当前消息 + 上报 token 用量 |

**理由**: AgentHub 的 `consume_stream` 已处理所有 StreamEvent 类型，不需要修改消费器。codex 的 `agent_message` delta 模式天然支持流式输出。

### D5: adapter 接口复用 — 不修改 `AgentPlatformAdapter` 接口

**选择**: `CodexCLIAdapter` 直接实现现有 `AgentPlatformAdapter` 接口（`name` 属性 + `stream(AdapterInput, cancel_event) -> AsyncIterator[StreamEvent]`）。

**理由**: `AgentRunner.execute_simple_run()` 的流程是：`adapter = registry.get_adapter(agent)` → `adapter_input = build_adapter_input(...)` → `stream = adapter.stream(adapter_input, cancel_event)` → `consume_stream(stream, ...)`。只要 adapter 产出正确的 StreamEvent，单聊和群聊自动可用，因为 Orchestrator 调度子 agent 也走 `execute_simple_run` → `run_with_args`。

### D6: 取消机制 — terminate 子进程

**选择**: 当 `cancel_event.is_set()` 时，`proc.terminate()` 然后等待 `proc.wait(timeout=5)`，超时则 `proc.kill()`。

**理由**: codex CLI 收到 SIGTERM 会清理 session 并退出。不能只关闭 stdin（codex 可能正在执行工具，不会立即响应 stdin EOF）。

### D7: codex 可执行文件路径解析

**选择**: 优先级 `agent.executable_path`（新字段，可选）→ `CODEX_EXECUTABLE` 环境变量 → PATH 上的 `codex`。

**理由**: Windows Store 版 Codex 桌面应用的 codex.exe 在 WindowsApps 目录下，受 ACL 保护无法被外部进程执行。用户需要安装独立的 `@openai/codex` CLI（`npm install -g @openai/codex`）。`executable_path` 字段允许用户指定非标准路径的 codex 二进制。

## Risks / Trade-offs

- **[WindowsApps ACL 限制]** Store 版 codex.exe 无法被 subprocess 调用 → 文档中明确说明需安装 npm 版 `@openai/codex` CLI，adapter 启动时检测 codex 不在 PATH 上则报清晰错误
- **[codex CLI 版本差异]** 不同 codex 版本的 JSON-RPC 事件 schema 可能不同 → adapter 做防御性解析，未知事件类型记 warning 日志但继续运行
- **[MCP bridge 进程泄漏]** codex 崩溃后 MCP bridge 子进程可能残留 → adapter 在 `finally` 块中同时 terminate codex 进程和 MCP bridge 进程
- **[session 无记忆]** 每次 run 创建新 session，跨 run 无上下文记忆 → Non-Goal，后续可扩展 session 续接（缓存 `conversation_id+agent_id → thread_id` 映射）
- **[审批能力降级]** codex 内置 bash/文件操作走 sandbox，不走 AgentHub pending writes → MCP 暴露的工具（write_artifact、deploy 等）仍可通过后端 API 做审批；codex 内置操作由 sandbox 限制边界
- **[BREAKING 迁移]** 已有 claude-code 类型 agent 将无法运行 → 迁移指南：改为 custom（openai 兼容）或 codex 类型
