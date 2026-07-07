# Design: P1 ReAct 循环上提与 Hooks 系统

## Context

当前架构中，Custom adapter 的 `stream` 方法包含完整的 ReAct 循环（`while turn < MAX_TURNS`），自管 `messages` 列表、工具执行、工具结果注入。AgentRunner 的 `execute_simple_run` 只调用 `adapter.stream()` 然后 `consume_stream` 消费事件。

这意味着：
- turn 间无法插入横切逻辑（压缩检查、hooks、审计）
- 工具执行逻辑（审批拦截、结果处理）硬编码在 adapter 内部
- `consume_stream` 中的 `on_tool_call` 回调只能做终端控制（stop），无法修改工具行为
- 确定性自动化（记忆固化、审计日志）只能散落在 `_post_run_memory_hook` 等独立函数中

CLI adapter（Claude Code / Codex）的循环由 CLI 子进程自管，不受此变更影响。

约束：不改 StreamEvent 协议、不改 DB schema、无新增外部依赖、CLI adapter 路径保持不变。

## Goals / Non-Goals

**Goals:**

- 将 SDK adapter 的 ReAct 循环从 adapter 上提到 AgentRunner
- 建立 HookRegistry 生命周期钩子系统，支持 10 种事件类型
- 工具执行从 adapter 移到 AgentRunner，统一经过 hooks 拦截
- 为 P2（Checkpoint & Resume）和 turn 间压缩铺路

**Non-Goals:**

- 不改 CLI adapter（Claude Code / Codex）的执行路径
- 不改 StreamEvent 类型定义（事件种类不变，产生方变化）
- 不实现 Checkpoint & Resume（P2）
- 不实现 turn 间压缩（P2，需要 P1 的循环上提基础）
- 不改前端代码（前端仍消费相同的 SSE 事件流）
- 不改 Orchestrator 编排逻辑（Orchestrator 调 `execute_simple_run` / `execute_orchestrator_run`，接口不变）

## Decisions

### Decision 1: `call_once` 是 SDK adapter 专属，CLI adapter 保持 `stream`

**选择**: `AgentPlatformAdapter` 新增 `call_once` 方法，但标记为 SDK 专属。CLI adapter 不实现（返回 `NotImplementedError`），AgentRunner 根据 `adapter_name` 选择路径。

**理由**:
- CLI adapter（Claude Code / Codex）的循环由 CLI 子进程自管，无法拆分为单轮调用
- 强制 CLI adapter 实现 `call_once` 会破坏其内部状态管理（session resume、stream-json 解析）
- `adapter_name in SDK_ADAPTERS` 判断已有先例（`execute_simple_run` 中多处使用）

**备选方案**: 让所有 adapter 都实现 `call_once` → 被否决，因为 CLI 的循环不在 Python 层，无法拆分。

### Decision 2: `call_once` 返回 `TurnResult` + 流式事件，AgentRunner 消费后决定下一步

**选择**: `call_once` 是一个 `AsyncIterator[StreamEvent]`，yield 所有单轮事件（message.start → part.* → message.end → tool.call *）。AgentRunner 的 `consume_stream` 消费后检查是否有 `tool_calls`：

- 有 tool_calls → 执行 hooks → 执行工具 → 注入结果 → 下一轮
- 无 tool_calls 或 finish_reason=stop → 结束循环

**TurnResult 结构**（AgentRunner 内部维护，不是 adapter 返回值）:
```python
@dataclass
class TurnResult:
    message_id: str
    text_content: str
    tool_calls: list[ToolCallInfo]  # [{id, name, args}]
    finish_reason: str | None
    usage: _MsgUsage
    assistant_message: dict  # 写入 messages 列表的 assistant dict
```

**理由**:
- `call_once` 保持 `AsyncIterator[StreamEvent]` 接口，与 `stream` 一致，降低学习成本
- `TurnResult` 由 `consume_stream` 从事件流中提取，不需要 adapter 额外返回
- AgentRunner 在 turn 间有完整的 `messages` 列表和 `TurnResult`，可以做任何干预

### Decision 3: `AdapterInput` 新增 `messages` 字段，AgentRunner 维护对话历史

**选择**: `AdapterInput` 新增 `messages: list[dict] | None` 字段。AgentRunner 初始化 `messages = [system, *history, user]`，每轮调 `call_once` 时传入当前 `messages`，adapter 不再自管 `messages`。

**理由**:
- adapter 不持有状态，每次 `call_once` 是无状态的纯函数调用
- AgentRunner 可以在 turn 间修改 `messages`（注入压缩摘要、裁剪 tool_result）
- 这是实现 turn 间压缩的前提

**备选方案**: adapter 维护 `messages`，通过回调暴露 turn 间钩子 → 被否决，因为 adapter 持有状态导致难以测试和取消。

### Decision 4: Hooks 用同步注册 + 异步派发，支持优先级

**选择**: `HookRegistry` 用 `dict[HookEvent, list[HookEntry]]` 存储，`HookEntry` 包含 `handler`、`priority`（int，小先执行）、`name`。派发时按 priority 排序依次 `await`。

**HookResult 控制流**:
```python
@dataclass
class HookResult:
    action: Literal["allow", "deny", "modify", "inject"]
    data: Any = None  # deny: reason; modify: modified_data; inject: injected_events
```

- `allow` — 继续执行（默认）
- `deny` — 阻止操作（pre_tool_use 可阻止工具执行）
- `modify` — 修改数据（post_tool_use 可修改工具结果）
- `inject` — 注入额外事件（on_stop 可注入总结消息）

**理由**:
- 同步注册 = 启动时一次性注册，运行时不可变（线程安全）
- 异步派发 = 钩子可能涉及 IO（写 DB、调 LLM）
- 优先级 = 审计日志（priority=0）先于业务逻辑（priority=10）

### Decision 5: 内置钩子放在 `hooks/` 目录，通过 agent 配置启用

**选择**: 内置钩子放在 `backend/app/services/hooks/` 目录，每个钩子文件导出 `register(registry: HookRegistry)` 函数。agent 配置新增 `hook_names: list[str]` 字段（类似 `tool_names`），运行时根据 agent 的 `hook_names` 启用对应钩子。

**内置钩子**:
- `audit_log` — pre_tool_use / post_tool_use 记录审计日志
- `memory_persist` — on_run_end 触发记忆固化
- `auto_compact` — post_turn 检查是否需要压缩（turn 间压缩）
- `tool_approval` — pre_tool_use 拦截需审批的工具（review mode / bash 黑名单）

**理由**:
- 钩子是可选的，不是所有 agent 都需要（CLI agent 自管工具）
- 通过 agent 配置启用 = 用户可控，不会全局影响
- `hooks/` 目录 = 可扩展，未来新增钩子不改核心代码

### Decision 6: 工具执行统一走 `ToolExecutor.execute_with_hooks`

**选择**: AgentRunner 的 ReAct 循环中，工具执行统一调用 `tool_registry.execute_with_hooks(name, args, ctx)`，内部流程：
1. 构造 `HookContext(event="pre_tool_use", tool_name, args)`
2. 派发 `pre_tool_use` hooks
3. 如果 `deny` → 返回错误结果
4. 如果 `modify` → 使用修改后的 args
5. 执行工具
6. 构造 `HookContext(event="post_tool_use", tool_name, result)`
7. 派发 `post_tool_use` hooks
8. 如果 `modify` → 使用修改后的 result
9. 返回最终结果

**理由**:
- 审批拦截从 `ToolExecutor.execute` 硬编码改为 hook，解耦安全逻辑与执行逻辑
- `post_tool_use` 可以修改结果（如脱敏、截断），这是当前无法做到的
- CLI adapter 的工具执行不受影响（CLI 自管工具，不走 `ToolExecutor`）

### Decision 7: 分阶段迁移，保留 `stream` 方法作为回退

**选择**: 
1. 第一阶段：新增 `call_once`，Custom adapter 同时实现 `stream` 和 `call_once`
2. 第二阶段：AgentRunner 的 `execute_simple_run` 检测 `adapter_name in SDK_ADAPTERS`，走新循环路径
3. 第三阶段：删除 Custom adapter 的 `stream` 方法中的循环逻辑（保留方法签名但标记 deprecated）

**理由**:
- 渐进式迁移，每阶段可独立测试和回退
- 如果 `call_once` 路径出问题，可以快速切回 `stream`
- CLI adapter 完全不受影响

## Risks / Trade-offs

- **[Custom adapter 行为变化]** → `call_once` 拆分后，DeepSeek 的 `reasoning_content` 回写逻辑需迁移到 AgentRunner → 迁移时保留 `assistant_msg["reasoning_content"]` 的写回逻辑
- **[工具执行时序变化]** → 工具从 adapter 内部执行移到 AgentRunner，`tool.call` 和 `tool.result` 事件的产生时序可能变化 → 保持事件顺序不变（tool.call → 执行 → tool.result）
- **[hooks 性能开销]** → 每轮 2 次 hook 派发（pre/post_turn），每次工具 2 次（pre/post_tool_use） → 内置钩子都是轻量操作（<1ms），审计日志异步写
- **[AdapterInput BREAKING]** → 新增 `messages` 字段 → 所有调用方在 `agent_runner.py` 内部，影响可控
- **[hooks 注册时序]** → 钩子在 app 启动时注册，agent 运行时不可变 → 如果需要动态注册，后续扩展

## Migration Plan

1. **Phase 1 — call_once + TurnResult（不改变运行时行为）**
   - Custom adapter 新增 `call_once` 方法（从 `stream` 中提取单轮逻辑）
   - AgentRunner 新增 `_run_react_loop` 函数（不接入 `execute_simple_run`）
   - 单元测试 `call_once` 和 `_run_react_loop`
   - 现有 `stream` 路径保持不变

2. **Phase 2 — HookRegistry + 内置钩子**
   - 新增 `hook_registry.py` 和 `hooks/` 目录
   - 实现 4 个内置钩子
   - Agent 模型新增 `hook_names` 字段（可选）
   - 单元测试 hooks 注册与派发

3. **Phase 3 — 切换 SDK 路径到 ReAct 循环**
   - `execute_simple_run` 中 SDK adapter 走 `_run_react_loop`
   - 工具执行移到 AgentRunner，走 `execute_with_hooks`
   - CLI adapter 路径不变
   - 集成测试 + 回归测试

4. **Phase 4 — 清理**
   - 删除 Custom adapter `stream` 方法中的循环逻辑
   - 迁移 `_post_run_memory_hook` 为 `on_run_end` hook
   - 迁移 `_maybe_auto_compact_hook` 为 `post_turn` hook

**回退策略**: Phase 3 中如果新路径出问题，通过 `settings.use_react_loop = False` 配置回退到 `stream` 路径。

## Open Questions

1. `hook_names` 是否需要存在 DB（`agents` 表新增列）还是存在 JSON 字段？→ 倾向 JSON 字段（`hook_names_list`，类似 `tool_names_list`），不改表结构
2. Orchestrator dispatch 的子 agent run 是否也走 ReAct 循环？→ 是的，`execute_simple_run` 是统一入口
3. `on_stop` hook 是否可以阻止 LLM 停止（强制继续）？→ 不支持，`on_stop` 是通知性的，不能阻止
