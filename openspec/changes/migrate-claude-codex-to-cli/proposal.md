# Proposal: migrate-claude-codex-to-cli

## Why

当前 Claude Code 和 Codex 适配器走 SDK/API 路线——Adapter 自己调用 LLM API、自己实现工具循环、AChat 管理 API key。这套架构有两个根本问题：

1. **重复造轮子**：Claude Code 和 Codex 本身就是成熟的 coding agent，自带工具集、沙箱、审批流、会话管理。AChat 用 SDK/API 重新实现了一遍（工具循环、安全策略、上下文管理），而这个逻辑已经在各家 CLI 里完整存在。
2. **跟不上厂商迭代**：厂商每次升级（新工具、新推理策略、新安全模型），AChat 都得跟进适配。走 CLI 路线则自动继承厂商升级。

参考 multica 的设计（`server/pkg/agent/`），将 Claude Code 和 Codex 从 SDK/API 路线切换为 **CLI 子进程路线**：AChat 启动厂商 CLI 作为子进程、通过 CLI 自身的协议（stream-json / JSON-RPC）通信、把 CLI 输出翻译为统一的 StreamEvent。**Custom adapter 保持不变**（继续走 OpenAI SDK + AChat 工具循环，因为用户自配的 OpenAI 兼容 API 没有对应 CLI）。

## What Changes

- **删除** `claude_adapter.py`（SDK 版，基于 `anthropic` SDK + 自写工具循环）
- **重写为** `claude_adapter.py`（CLI 版：`spawn claude -p --output-format stream-json`，监听事件流）
- **新增** `codex_adapter.py`（CLI 版：`spawn codex app-server --listen stdio://`，JSON-RPC 2.0 通信）—— 当前 Python 后端 codex 是 deferred 状态
- **新增** `cli_base.py`（CLI 适配器公共基类：子进程生命周期管理、stdin/stdout 管道、超时/取消）
- **修改** `AdapterInput`：新增 `executable_path`、`extra_env`、`mcp_config`
- **修改** `Agent` 模型：新增 `executable_path`、`protocol_family` 字段
- **修改** `AgentRunner.build_adapter_input`：CLI agent 跳过后 API key 解析、工具注入、历史构建
- **新增** AChat MCP Bridge：把 `write_artifact` 等 AChat 专属工具通过 MCP Server 暴露给 CLI agent
- **不影响** Custom adapter、Mock adapter、StreamEvent 协议、`consume_stream`/`persist_event`、前端渲染

## Capabilities

### Modified Capabilities

- `adapters`：修改 ClaudeCodeAdapter 和 CodexAdapter 的实现路线（SDK→CLI），新增 CLI 进程管理契约，新增 AChat MCP Bridge 契约。Custom adapter 需求不变。

### New Capabilities

- 无新增 capability。所有变更在 `adapters` 内完成。

## Impact

- **后端适配器层**：`claude_adapter.py` 完全重写（删除 ~400 行 SDK 代码，重写为 ~400 行 CLI wrapper）；`codex_adapter.py` 从 deferred 变为 ~500 行 CLI wrapper；新增 `cli_base.py` ~150 行；新增 `agenthub_mcp.py` ~200 行
- **AdapterInput**：扩 3 个可选字段（CLI agent 专用）
- **Agent 模型**：扩 2 个可空字段 + migration
- **AgentRunner**：`build_adapter_input` 分 SDK/CLI 两条路径，工具注入针对 adapter 类型区分
- **AgentRegistry**：注册 CLI 版适配器替换旧版
- **DB**：DDL migration 新增列，无破坏性变更
- **不影响**：StreamEvent 协议、MessagePart 结构、前端渲染、Orchestrator、RAG/记忆系统、Custom adapter
