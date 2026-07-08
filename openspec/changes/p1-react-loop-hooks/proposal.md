# Proposal: P1 ReAct 循环上提与 Hooks 系统

## Why

当前 AChat 的 Custom adapter 将 ReAct 循环（`while turn < MAX_TURNS`）封装在 adapter 内部，AgentRunner 只做单次 `consume_stream` 消费。这意味着 turn 间的上下文管理、工具审批拦截、自动压缩、错误重试等横切关注点无法在 L3 服务层介入。同时系统缺少生命周期钩子机制，确定性自动化（如工具执行前后记录审计日志、停止时触发记忆固化）只能硬编码散落在各处。

P0 的多层压缩已解决上下文体积问题，但它仍在读取路径上操作。要实现 turn 间压缩（每轮结束后检查是否需要压缩），必须将循环控制权从 adapter 上提到 AgentRunner。P1 是 P2（Checkpoint & Resume）的前置依赖。

## What Changes

### O2: ReAct 循环上提 — Adapter 接口新增 `call_once`

- **BREAKING**: `AgentPlatformAdapter` 新增 `call_once(input, cancel_event) -> AsyncIterator[StreamEvent]` 方法，执行单次 LLM 调用（一个 turn）
- Custom adapter 的 `stream` 方法重构为 AgentRunner 中的 `while` 循环，每轮调 `call_once`
- `call_once` 返回的 `TurnResult` 包含 `assistant_message`、`tool_calls`、`finish_reason`、`usage`
- AgentRunner 在 turn 间执行：取消检查 → hooks 派发 → 上下文压缩检查 → 工具执行 → 下一轮
- CLI adapter（Claude Code / Codex）不受影响：它们的 `stream` 方法保持不变（CLI 自管循环），`call_once` 是 SDK adapter 专属
- `AdapterInput` 新增 `messages: list[dict]` 字段，由 AgentRunner 维护完整对话历史，adapter 不再自己维护 `messages` 列表

### O3: Hooks 系统 — 生命周期钩子注册与派发

- 新增 `HookRegistry`，支持注册 `async def handler(context: HookContext) -> HookResult` 的钩子函数
- 钩子事件类型（6 种核心 + 4 种扩展）：
  - `pre_turn` / `post_turn` — 每轮 LLM 调用前后
  - `pre_tool_use` / `post_tool_use` — 工具执行前后（可拦截/修改结果）
  - `on_stop` — LLM 停止调用工具时
  - `on_error` — 运行出错时
  - `on_run_start` / `on_run_end` — 运行生命周期
  - `on_message_end` — 消息结束时
- `HookResult` 支持 `allow` / `deny` / `modify` / `inject` 四种控制流
- 内置钩子：审计日志、记忆固化触发、上下文压缩检查、工具审批拦截
- 钩子注册通过 `app.state.hook_registry` 全局单例，agent 可声明 `hook_names` 启用特定钩子组

### O11: 工具执行增强 — turn 间工具结果注入与审批桥接

- 工具执行从 adapter 内部移到 AgentRunner（SDK 路径），`call_once` 只返回 `tool_calls`，不执行
- AgentRunner 统一执行工具：`pre_tool_use` hook → 审批检查 → 执行 → `post_tool_use` hook → 结果注入回 `messages`
- `ToolExecutor` 新增 `execute_with_hooks(tool_name, args, ctx, hooks)` 方法
- 审批拦截（review mode / bash 黑名单）从 `ToolExecutor.execute` 硬编码改为 `pre_tool_use` hook 注册
- 工具结果注入 `messages` 后，AgentRunner 决定是否继续下一轮

## Capabilities

### New Capabilities

- `lifecycle-hooks`: 生命周期钩子注册、派发与控制流协议（HookRegistry、HookContext、HookResult、6+4 种事件类型）

### Modified Capabilities

- `adapters`: 新增 `call_once` 接口要求；`AdapterInput` 新增 `messages` 字段；SDK adapter 不再自管循环
- `tools`: 工具执行从 adapter 移至 AgentRunner；新增 `execute_with_hooks`；审批拦截改为 hook 注册
- `stream-events`: 不新增事件类型，但 `tool.call` / `tool.result` 事件的产生方从 adapter 变为 AgentRunner（SDK 路径）

## Impact

### 代码影响

- `backend/app/adapters/base.py` — `AgentPlatformAdapter` 新增 `call_once` 抽象方法（SDK 专属，CLI adapter 可不实现）
- `backend/app/adapters/custom_adapter.py` — `stream` 方法拆分为 `call_once`（单轮 LLM 调用 + 流式输出），不再有 `while` 循环
- `backend/app/services/agent_runner.py` — `execute_simple_run` 新增 ReAct 循环逻辑（SDK 路径），`consume_stream` 增强
- `backend/app/services/hook_registry.py` — **新增文件**，HookRegistry + HookContext + HookResult
- `backend/app/services/hooks/` — **新增目录**，内置钩子实现
- `backend/app/tools/registry.py` — `ToolExecutor` 新增 `execute_with_hooks`
- `backend/app/tools/base.py` — `ToolContext` 新增 `hook_registry` 引用

### API 影响

- 无 HTTP API 变更
- **BREAKING**（内部接口）：`AgentPlatformAdapter` 新增 `call_once` 方法，第三方 adapter 需实现
- CLI adapter（Claude Code / Codex）不受影响，继续使用 `stream`

### 依赖影响

- 无新增外部依赖
- 不改 DB schema
- 不改 StreamEvent 协议（事件类型不变，产生方变化）

### 测试影响

- `custom_adapter` 的 `call_once` 单元测试
- `agent_runner` ReAct 循环的单元测试（多轮、取消、MAX_TURNS）
- `hook_registry` 注册与派发的单元测试
- `pre_tool_use` hook 拦截工具调用的集成测试
- 回归测试：现有 CLI adapter 路径不受影响

### 迁移风险

- Custom adapter 的 `stream` 方法被拆分，现有直接调用 `adapter.stream()` 的地方需改为 `agent_runner` 的循环
- `AdapterInput` 新增 `messages` 字段是 **BREAKING**，但所有调用方在 `agent_runner.py` 内部，影响可控
- 工具执行从 adapter 移到 AgentRunner，现有 `custom_adapter.py` 中的工具执行逻辑需迁移
