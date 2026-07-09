## Context

Custom adapter 是 AChat 的 SDK 路线 adapter，通过 OpenAI Chat Completions API + 自驱 ReAct loop 执行工具。当前工具来源仅限 `tool_registry.resolve(input.tool_names)`——只能调用 AChat 内置工具。

Spec 15 已设计了通用 MCP 接入方案，但将 Custom adapter 的 MCP 客户端层推迟到 P1。本变更将其前置，让 Custom agent 作为 MCP client 连接用户配置的外部 MCP server。

Custom adapter 当前有两条执行路径：
- **`_run_react_loop()`**（新路径，`use_react_loop=true` 默认开启）：AgentRunner 管循环，`call_once()` 单轮 LLM 调用，工具执行在 `_run_react_loop()` 内通过 `tool_registry.execute_with_hooks()` 完成。
- **`adapter.stream()`**（旧路径 fallback）：adapter 自管循环 + 工具执行。

本变更只改新路径（`call_once()` + `_run_react_loop()`），旧路径 `stream()` 保持不变（无 MCP 支持）。

## Goals / Non-Goals

**Goals:**

- Custom agent 能连接用户配置的外部 MCP server（stdio + SSE）
- MCP 工具注入到 ReAct loop，与内置工具并列供 LLM 选择
- MCP 工具调用复用现有 `tool.call` / `tool.result` StreamEvent，不新增事件类型
- `ask` trust 级别的 MCP 工具走 pending 审批门
- 左边栏提供 MCP server 管理面板
- Agent builder 提供 MCP server 勾选

**Non-Goals:**

- 不改 CLI adapter（Claude Code / Codex）的 MCP 接入——它们走 SDK 自管的 MCP，是 P0 的工作
- 不做 per-conversation 连接池——首版 per-run 连接 + 结束 teardown
- 不做 OAuth-flow 鉴权的远程 MCP——只支持 stdio 命令 + 带静态 header 的 SSE
- 不改 `adapter.stream()` 旧路径——MCP 仅在 ReAct loop 路径生效
- 不做 MCP server 的发布/托管——AChat 只作为 MCP client

## Decisions

### Decision 1: per-run 连接 + 结束 teardown

**选择**: 每次 `execute_simple_run()` 开始时连接所有启用的 MCP server，run 结束/中止时关闭所有连接、杀 stdio 进程。

**理由**:
- 干净——无跨 run 状态泄漏，符合 adapter「不持久状态」原则
- 简单——不需要引用计数或连接池管理
- stdio server 每次连接 spawn 子进程，run 结束杀掉，不会有孤儿进程

**备选方案**: per-conversation 连接池 → 被否决，引入跨 run 状态管理复杂度，作为 P2 优化

### Decision 2: 工具命名 `mcp__<serverName>__<toolName>`

**选择**: 对齐 Claude Code / Codex SDK 既有约定，外部 MCP 工具统一命名 `mcp__<serverName>__<toolName>`。

**理由**:
- 避免与内置工具冲突（内置工具名如 `fs_read` / `write_artifact`）
- 前缀 `mcp__` 作为路由信号——`_run_react_loop()` 中工具执行时判断前缀路由到 MCP client 或 `tool_registry`
- 用户/LLM 可直观识别工具来源

### Decision 3: `ask` 审批粒度 = per-tool-per-conversation

**选择**: trust 级别为 `ask` 的 server，其某个工具在某个会话内**首次**调用时弹审批，批准后该会话内该工具免再问；拒绝则该次调用返回 `isError`。

**理由**:
- 每次都问太吵——一个工具可能被调用十几次
- 整个 server 一次批太粗——同一 server 可能既有安全的读、又有危险的写
- per-tool-per-conversation 兼顾安全与可用
- 复用现有 `await_pending_decision` + `pending_writes` 机制，新建 `pending_mcp_calls` store

### Decision 4: MCP 客户端层放在 `backend/app/mcp/`

**选择**: 新建 `backend/app/mcp/` 模块，包含 `client_manager.py`（连接管理）和 `__init__.py`。

**理由**:
- 独立于 `adapters/`（adapter 不持有 MCP 状态）和 `tools/`（MCP 工具不在 tool_registry 中）
- `client_manager.py` 由 `agent_runner.py` 的 `execute_simple_run()` 在 run 生命周期内创建和销毁
- 对 adapter 透明——`call_once()` 通过 `AdapterInput` 拿到 MCP 工具声明列表，不需要知道 MCP client 的存在

### Decision 5: MCP 工具声明通过 AdapterInput 传递

**选择**: 在 `AdapterInput` 中新增 `mcp_tools: list[dict] | None` 字段，由 `execute_simple_run()` 在 run 开始时连接 MCP server、调用 `listTools()`、转换为 OpenAI function-calling 格式后传入。`call_once()` 将其与内置工具声明合并。

**理由**:
- adapter 不直接接触 MCP client——保持 adapter 的纯翻译角色
- 工具声明在 run 开始时就确定（MCP server 连接成功后 `listTools()` 一次）
- 工具执行时由 `_run_react_loop()` 路由——`mcp__` 前缀的调用走 `mcp_manager.call_tool()`，否则走 `tool_registry.execute_with_hooks()`

**备选方案**: 让 adapter 直接持有 MCP client → 被否决，破坏 adapter 无状态原则

### Decision 6: 安全模型 — 显式 opt-in + ask 审批门

**选择**:
1. Server 由用户手动登记（左边栏 MCP 管理面板）
2. Agent 由用户手动勾选启用（Agent builder）
3. `trust` 级别：
   - `always`: 直接放行
   - `ask`（默认）: per-tool-per-conversation 审批门
4. stdio 命令不做硬白名单——登记时完整展示 command/args/env + 要求「我信任此 server」确认

**理由**:
- 外部 MCP 运行任意代码 / 访问外部网络，绕过沙箱——这是用户授予的信任
- 本地单用户场景下「用户登记 = 知情同意」（等价于自己在终端跑）
- 不做硬白名单因为 MCP server 命令五花八门，枚举不现实（Spec 15 §11 决策 4 已定）

### Decision 7: 密钥脱敏 — headers/env 存 DB + UI 脱敏

**选择**: headers / env 密钥存 DB（同 `app_settings`，不引 keychain）；API 列表接口不回明文（只露后几位）；支持值里写 `${ENV_NAME}` 占位以引用 `.env.local`。

**理由**:
- 对齐 CLAUDE.md §5.4 不过度加固的取向
- MCP 密钥是 per-server 维度，不套用 LLM 的三层 key 优先级

### Decision 8: 旧路径 `adapter.stream()` 不加 MCP 支持

**选择**: 只改 `_run_react_loop()` + `call_once()` 路径。`adapter.stream()` 旧路径不支持 MCP 工具。

**理由**:
- `use_react_loop` 默认开启（`config.py:103`），旧路径是 fallback
- 改两条路径增加复杂度且旧路径终将废弃
- 如果用户关了 `use_react_loop`，MCP 工具不生效——在文档中说明

## Risks / Trade-offs

- **[安全口子]** 外部 MCP 不在 AChat 的沙箱保证范围内 → Mitigation: 显式 opt-in + ask 审批门 + UI 明确警告
- **[进程泄漏]** stdio MCP server 是子进程，run 中止时未正确清理会泄漏 → Mitigation: `execute_simple_run()` 的 `finally` 块确保 `close_all()`；复用 `cli_base.py` 的 `killProcessTree` 逻辑
- **[工具名冲突]** 不同 MCP server 可能暴露同名工具 → Mitigation: 命名空间 `mcp__<serverName>__<toolName>` 保证全局唯一
- **[连接失败]** 某 server 连接失败不应崩整个 run → Mitigation: `connect_all()` 对每个 server 独立 try/except，失败的 server 标记不可用 + warning log，其他 server 正常工作
- **[工具数膨胀]** 太多 MCP 工具可能超出 LLM context window → Mitigation: 工具描述截断；未来可做按需加载
- **[Windows 兼容]** stdio 子进程在 Windows 上需隐藏窗口 → Mitigation: 复用 `conpty.py` / `hide_window` flag

## Migration Plan

1. **Phase 1 — 数据模型 + MCP 客户端核心**
   - 新增 `McpServer` 模型 + `agents.mcp_server_ids` 列
   - 实现 `McpClientManager`（connect / listTools / callTool / close）
   - 后端 API CRUD

2. **Phase 2 — Adapter + ReAct loop 集成**
   - `build_adapter_input()` 解析 `mcp_server_ids` + 连接 MCP server + 构建 `mcp_tools`
   - `call_once()` 合并 MCP 工具声明
   - `_run_react_loop()` 工具执行路由
   - `execute_simple_run()` 生命周期管理

3. **Phase 3 — 审批门**
   - `pending_mcp_calls` store
   - `ask` trust 级别的工具调用走审批
   - SSE 事件 + 前端审批 UI

4. **Phase 4 — 前端 UI**
   - 左边栏 MCP server 管理面板
   - Agent builder MCP server 勾选
   - 测试连接功能

**回退策略**: MCP 连接失败时不阻断 run——标记 MCP 工具不可用，agent 仍可使用内置工具正常对话。`McpClientManager` 的所有调用点都有 try/except 降级路径。

## Open Questions

- MCP server 的 `test connection` API 应在后端建立临时连接还是由前端直接连接？→ 倾向后端代理（前端不应直接 spawn 子进程），后端建临时连接 + listTools 预览后关闭。
- Orchestrator 群聊场景下，子任务的 Custom agent 是否也支持 MCP？→ 天然支持——`_run_child_task` 走 `execute_simple_run`，MCP 生命周期由 run 管理。
