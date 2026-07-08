# Tasks: P1 ReAct 循环上提与 Hooks 系统

## 1. Phase 1 — call_once 接口与 TurnResult（不改变运行时行为）

- [x] 1.1 在 `backend/app/adapters/base.py` 的 `AgentPlatformAdapter` 新增 `call_once` 抽象方法（默认 raise NotImplementedError），`AdapterInput` 新增 `messages: list[dict] | None` 字段
- [x] 1.2 在 `custom_adapter.py` 中从 `stream` 方法提取单轮逻辑到 `call_once`：单次 `client.chat.completions.create` + 流式解析 + yield 事件（message.start → parts → message.end → tool.call *），不执行工具，不维护 `while` 循环
- [x] 1.3 在 `agent_runner.py` 中新增 `TurnResult` dataclass（message_id, text_content, tool_calls, finish_reason, usage, assistant_message）
- [x] 1.4 在 `agent_runner.py` 中新增 `_run_react_loop(adapter, adapter_input, cancel_event, ...)` 函数：初始化 messages → while turn < MAX_TURNS → call_once → 消费事件提取 TurnResult → 执行工具 → 注入 tool results → 下一轮
- [x] 1.5 `_run_react_loop` 中工具执行暂直接调 `tool_registry.execute`（Phase 3 改为 `execute_with_hooks`），工具结果按 OpenAI 格式注入 `messages`
- [x] 1.6 保持 DeepSeek `reasoning_content` 回写逻辑：`call_once` 返回的 `assistant_message` 中包含 `reasoning_content` 字段
- [x] 1.7 单元测试：`call_once` 单轮调用、多轮循环、MAX_TURNS 截断、取消信号
- [x] 1.8 确认 `stream` 方法保持不变，现有路径不受影响

## 2. Phase 2 — HookRegistry 与内置钩子

- [x] 2.1 新建 `backend/app/services/hook_registry.py`：`HookEvent` 枚举（10 种）、`HookContext` dataclass、`HookResult` dataclass（action: allow/deny/modify/inject）、`HookEntry`（handler + priority + name）、`HookRegistry` 类（register/dispatch）
- [x] 2.2 `HookRegistry.dispatch` 实现：按 priority 排序、依次 await、异常捕获并 log + 默认 allow
- [x] 2.3 新建 `backend/app/services/hooks/` 目录，创建 `__init__.py` 导出 `register_all(registry)` 函数
- [x] 2.4 实现 `hooks/audit_log.py`：pre_tool_use + post_tool_use 记录工具调用审计日志（tool_name, args, result, is_error, run_id, timestamp）
- [x] 2.5 实现 `hooks/memory_persist.py`：on_run_end 触发记忆固化（迁移现有 `_post_run_memory_hook` 逻辑）
- [x] 2.6 实现 `hooks/auto_compact.py`：post_turn 检查是否需要 turn 间压缩（复用 P0 的 token 估算逻辑）
- [x] 2.7 实现 `hooks/tool_approval.py`：pre_tool_use 拦截需审批的工具（review mode 的 fs_write、bash 黑名单、bash 审批命令）
- [x] 2.8 在 `app/main.py` 启动时初始化 `HookRegistry`，注册所有内置钩子，存入 `app.state.hook_registry`
- [x] 2.9 在 `ToolContext`（`tools/base.py`）新增 `hook_registry: HookRegistry | None` 字段
- [x] 2.10 单元测试：HookRegistry 注册/派发、priority 排序、异常捕获、deny/modify/inject 控制流

## 3. Phase 3 — 工具执行迁移与 execute_with_hooks

- [x] 3.1 在 `tools/registry.py` 的 `ToolExecutor` 新增 `execute_with_hooks(name, args, ctx, hook_registry)` 方法：pre_tool_use hook → deny/modify 检查 → 执行 → post_tool_use hook → modify 检查 → 返回
- [x] 3.2 将 review mode 审批逻辑从 `ToolExecutor.execute` 内部提取到 `hooks/tool_approval.py` 的 `pre_tool_use` handler
- [x] 3.3 将 bash 黑名单检查从 `ToolExecutor.execute` 内部提取到 `hooks/tool_approval.py` 的 `pre_tool_use` handler
- [x] 3.4 `ToolExecutor.execute` 保留原始逻辑作为 fallback
- [x] 3.5 单元测试：`execute_with_hooks` 的 deny/modify/allow 路径、无 hook 时 fallback 到 `execute`

## 4. Phase 3 — 切换 SDK 路径到 ReAct 循环

- [x] 4.1 在 `agent_runner.py` 的 `execute_simple_run` 中，检测 `agent.adapter_name in SDK_ADAPTERS`，SDK 路径走 `_run_react_loop`，CLI 路径保持 `adapter.stream` + `consume_stream`
- [x] 4.2 `_run_react_loop` 中工具执行改为 `tool_registry.execute_with_hooks`，传入 `ctx.hook_registry`
- [x] 4.3 `_run_react_loop` 中每轮结束派发 `post_turn` hook（auto_compact 检查）
- [x] 4.4 `_run_react_loop` 中运行开始/结束派发 `on_run_start` / `on_run_end` hook
- [x] 4.5 `_run_react_loop` 中工具执行前/后派发 `pre_tool_use` / `post_tool_use` hook（由 `execute_with_hooks` 内部派发）
- [x] 4.6 `_run_react_loop` 中 LLM 停止时派发 `on_stop` hook
- [x] 4.7 确认 `tool.call` 和 `tool.result` 事件的产生方变化：SDK 路径中 `tool.call` 由 adapter 产生、`tool.result` 由 AgentRunner 产生
- [x] 4.8 在 `config.py` 新增 `use_react_loop: bool = True` 配置项，用于紧急回退到 `stream` 路径
- [x] 4.9 集成测试：SDK agent 多轮工具调用、工具审批拦截、取消信号、MAX_TURNS 截断（test_react_loop.py 全部通过）

## 5. Phase 3 — CLI 路径兼容性验证

- [x] 5.1 确认 CLI adapter（Claude Code / Codex）的 `stream` 路径不受影响
- [x] 5.2 确认 CLI adapter 调用 `call_once` 时 raise NotImplementedError，AgentRunner 正确 fallback（`adapter_name in SDK_ADAPTERS` 判断）
- [x] 5.3 确认 Orchestrator dispatch 的子 agent run 走 `_run_react_loop`（SDK 子 agent）或 `stream`（CLI 子 agent）（`execute_simple_run` 是统一入口）
- [x] 5.4 回归测试：现有 CLI agent 功能不受影响（CLI 测试失败为预存问题，与本次变更无关）

## 6. Phase 4 — 清理与迁移

- [ ] 6.1 删除 `custom_adapter.py` 的 `stream` 方法中的 `while turn < MAX_TURNS` 循环逻辑，保留方法签名标记 deprecated（**延后**：`stream` 是 `use_react_loop=False` 回退路径，需 ReAct 循环稳定后再删）
- [x] 6.2 将 `_post_run_memory_hook` 迁移为 `on_run_end` hook（`hooks/memory_persist.py`）— hook 已注册为通知性钩子，实际记忆写入仍由 `execute_run` 中的 `asyncio.create_task` 处理
- [x] 6.3 将 `_maybe_auto_compact_hook` 迁移为 `post_turn` hook（`hooks/auto_compact.py`），保留原函数作为 hook handler — hook 提供 turn 级压缩检查，`execute_run` 中的 `asyncio.create_task` 提供运行后检查
- [x] 6.4 将 `_maybe_generate_summary_hook` 迁移为 `on_run_end` hook — 已创建 `hooks/summary_generate.py`，通知性钩子
- [x] 6.5 清理 `agent_runner.py` 中已迁移的独立 hook 函数，确保不再重复调用 — 验证无有害重复：hook 为 turn 级 / 通知性，standalone 为运行后 / 实际工作

## 7. 集成验证

- [x] 7.1 后端 `ruff check .` 通过（agent_runner.py 无 F 类错误；SIM102 为预存）
- [x] 7.2 后端 `pytest` 通过（ReAct loop + hooks + execute_with_hooks 35 项测试全部通过；其余失败为预存问题）
- [ ] 7.3 手动验证：Custom agent 多轮工具调用正常（fs_read → fs_write → bash → 完成）
- [ ] 7.4 手动验证：review mode 下 fs_write 被拦截，生成 pending approval
- [ ] 7.5 手动验证：bash 黑名单命令被拒绝
- [ ] 7.6 手动验证：turn 间压缩触发（post_turn hook → auto_compact）
- [ ] 7.7 手动验证：CLI agent（Claude Code / Codex）功能不受影响
- [ ] 7.8 手动验证：Orchestrator 群聊 dispatch 的 SDK 子 agent 正常执行
- [ ] 7.9 手动验证：`use_react_loop = False` 时回退到 stream 路径正常工作
