## Context

当前前端在 Agent 对话中的思考与工具调用展示存在三个问题：

1. **思考内容默认折叠**：`ThinkingPart` 组件初始 `open=false` + `line-clamp-1`，用户看不到 AI 正在推理什么。流式思考内容逐字追加时用户只能看到第一行被截断的文字，体验割裂。

2. **无任何耗时反馈**：后端的 `part.start`（含 `timestamp`）、`part.end`（含 `timestamp`）、`tool.call`（含 `timestamp`）、`tool.result`（含 `timestamp`）事件都携带时间戳，但 `app-store.ts` 的 reducer 完全忽略了这些 `timestamp`，既不存入 `MessagePart`，也不在 UI 展示。历史消息从 DB 加载时同样没有时长信息。

3. **工具运行中无实时计时**：`ToolUsePart` 的 `running` 态只显示 spinner + "调用中"，用户不知道工具已经跑了多久。

4. **Run 间隙无执行反馈**：ReAct 循环中 `message.end` 后到下一条 `message.start` 之间有间歇（Agent 在调用工具、等待结果、准备下一轮），此时 message 级 `Loader2` spinner 消失（因为 `message.status` 从 `streaming` 变为 `complete`），但 run 仍在进行。store 已有 run 级别 `status: 'running'` 数据（`runsByConv`），且 `message-input.tsx` 已用 `useTopLevelRunningRuns` 锁输入框，但消息列表未用于视觉反馈。用户误以为 Agent 挂了或完成了。当前 spinner 也太小（`size-3` = 12px 灰色），容易忽略。

当前数据流：

```
后端事件 (BaseEvent.timestamp 已有)
     │
     ▼
store reducer (忽略 timestamp)
     │
     ▼
MessagePart (无时间字段)
     │
     ▼
UI 渲染 (无时长)
```

## Goals / Non-Goals

**Goals:**

- thinking / tool_use / tool_result 的 `MessagePart` 类型新增可选时间字段，store 从事件 `timestamp` 捕获
- 后端 `persist_event` 在写入 part dict 时附带时间戳，持久化到 DB（JSON 列）
- ThinkingPart 三态 UI：streaming-open（流式展开 + "深度思考中" + 限制可见高度）、completed-collapsed（完全折叠 0 行 + 显示耗时 + 平滑动画）、user-expanded（手动展开）
- ToolUsePart 运行中实时计时（`setInterval`），完成显示最终耗时
- ToolCluster 展开后各工具显示各自耗时，header 显示总耗时
- `message.end` 时在消息气泡底部显示整轮回答总耗时
- 消息列表底部显示 run 级别持久执行指示器（typing dots + 阶段文本），ReAct 循环间隙也不消失，直到 `run.end` 才移除
- agent avatar 在 run 活跃时显示脉冲环（pulsing ring），不只是 message streaming 时
- 历史消息从 DB 加载时也能显示思考/工具时长（因为时间字段持久化在 parts JSON 中）
- 所有新增字段为可选，旧数据自然降级为不显示时长

**Non-Goals:**

- 不修改 `StreamEvent` 事件类型（事件协议不变，只是 store 开始读 `timestamp`）
- 不修改 DB schema（parts 列仍是 JSON，新增字段自然嵌入）
- 不修改后端 adapter 层（adapter 照常产事件，时间捕获在 `persist_event` + store reducer 层完成）
- 不做多轮 thinking 合并展示（一个 ReAct turn 里的多轮 thinking 各自独立显示）
- 不做思考内容的复制按钮
- 不引入新依赖
- 不做实时进度条或百分比估算（无法准确计算 Agent 执行进度）

## Decisions

### D1: 时间字段嵌入 MessagePart 类型（方案 A）

**选择**：在 `MessagePart` 的 `thinking` / `tool_use` / `tool_result` 类型上新增可选字段 `startedAt` / `endedAt`，由 store reducer 从事件 `timestamp` 捕获，后端 `persist_event` 写入 part dict 时附带。

**类型变更**：

```typescript
// thinking
| { type: 'thinking'; content: string; startedAt?: number; endedAt?: number }

// tool_use
| { type: 'tool_use'; callId: string; toolName: string; args: unknown; startedAt?: number }

// tool_result
| { type: 'tool_result'; callId: string; result: unknown; isError: boolean; endedAt?: number }
```

**数据流**：

```
part.start (timestamp=T1)
     │ store: msg.parts[i].startedAt = T1
     ▼
part.delta (thinking.append)
     │ store: content += text
     ▼
part.end (timestamp=T2)
     │ store: msg.parts[i].endedAt = T2
     ▼
UI: duration = endedAt - startedAt
```

```
tool.call (timestamp=T3)
     │ store: tool_use part.startedAt = T3
     ▼
tool.result (timestamp=T4)
     │ store: tool_result part.endedAt = T4
     ▼
UI: duration = T4 - T3
```

**理由**：
- 持久化：时间字段随 part JSON 存入 DB，历史消息也能显示时长
- 向后兼容：可选字段（`?`），旧 part 无时间字段时 UI 降级为不显示时长
- 无新表/列：parts 列已是 JSON，新字段自然嵌入，无 DB migration
- 前后端对称：`src/shared/types.ts` 加字段，`backend/app/schemas/messages.py` 同步加

**备选**：store 层维护独立 `partTimingMap`（不持久化，刷新丢失，历史消息无时长）。

### D2: ThinkingPart 三态状态机

**选择**：ThinkingPart 组件根据 message status + part 状态分为三个显示态：

```
                         message.status === 'streaming'
                         AND part is last content part
                     ┌──────────────────────────┐
                     │  STREAMING_OPEN          │
                     │  "深度思考中..."           │
                     │  流式文本逐字显示          │
                     │  max-h-40 overflow-auto   │
                     │  自动滚动到底             │
                     └────────────┬─────────────┘
                                  │
                     part.end 或 message.status === 'complete'
                                  │ (自动 + 平滑动画)
                                  ▼
                     ┌──────────────────────────┐
                     │  COMPLETED_COLLAPSED     │
                     │  "已深度思考 · 12.3s"     │
                     │  0 行内容显示             │
                     │  [展开] 按钮             │
                     └────────────┬─────────────┘
                                  │ 用户点击
                                  ▼
                     ┌──────────────────────────┐
                     │  USER_EXPANDED           │
                     │  "已深度思考 · 12.3s"     │
                     │  完整思考内容             │
                     │  [收起] 按钮             │
                     └──────────────────────────┘
```

**判断"正在流式"的条件**：
- `message.status === 'streaming'`
- 且 thinking part 是 parts 数组中最后一个有 content 的 part（即后续没有 text / tool_use 等 part）
- 或更精确地：store 跟踪 `activePartIndex`（当前正在追加 delta 的 part index），thinking part index === activePartIndex

**实现简化**：用 `message.status === 'streaming'` + thinking part 是最后一个 part 来判断 streaming 态。当 `message.status` 变为 `complete` / `error` / `aborted` 时自动切到 collapsed。用户展开后用 local state 记住 `userOpened`。

**平滑动画**：从 streaming-open 到 completed-collapsed 使用 CSS `transition` + `max-height` 变化，配合 `opacity` 淡出内容区域。

### D3: 工具运行中实时计时

**选择**：`ToolUsePart` 在 `running` 态时启动 `setInterval(1000)` 更新已运行秒数，在 `tool.result` 到达后清除 interval 并显示最终耗时。

```
tool.call 到达 (startedAt = T)
     │
     ▼  state = 'running'
     │
     │  setInterval(() => setElapsed(now - T), 1000)
     │  显示: "调用中 · 3s..."
     │
tool.result 到达 (endedAt = T')
     │  clearInterval
     ▼  state = 'success' / 'error'
     │
     │  显示: "已完成 · 3.2s" / "失败 · 3.2s"
```

**Hook 封装**：提取 `useElapsedTimer(startedAt: number | undefined, isActive: boolean)` 自定义 hook，返回已运行毫秒数。内部用 `useEffect` + `setInterval`，`isActive` 为 false 时不启动。

**ToolCluster 总耗时**：
- cluster header 显示 `max(endedAt) - min(startedAt)`（所有工具的总时间跨度）
- 展开后每个 ToolUsePart 独立显示各自耗时
- 如果所有工具都在运行中，cluster header 显示 "X 进行中 · Ys..."（用最早 startedAt 计时）

### D4: 思考流式时的可见高度限制与自动滚动

**选择**：streaming-open 态的 ThinkingPart 内容区域限制 `max-h-40`（约 160px），`overflow-y: auto`，自动滚动到底部跟随流式追加。

```
┌──────────────────────────────────┐
│ 🧠 深度思考中...                   │  ← header 行
├──────────────────────────────────┤  ← max-h-40, overflow-auto
│ (流式文本逐字显示)                  │
│ ...                               │
│ (超出高度自动滚动)                   │
└──────────────────────────────────┘
```

**自动滚动**：在 `ThinkingPart` 内部用 `useEffect` + `useRef` 监听 `content` 变化时 `scrollHeight` 增长，将 `scrollTop` 设为 `scrollHeight`。

**折叠后释放空间**：completed-collapsed 态高度为单行 header 高度，不再占据内容区域空间。

### D5: 整轮回答总耗时显示

**选择**：在 `message.end` 事件到达时，从 `run.start` 事件（已有 `timestamp`）和 `message.end` 事件（已有 `timestamp`）计算总耗时，显示在消息气泡底部。

**数据来源**：
- store 已有 `run.start` 事件（含 `timestamp`），但当前未单独存储 run start time
- 方案：store 在处理 `run.start` 事件时记录 `runStartTimestampByRunId[runId] = timestamp`
- `message.end` 时取 `message.runId` 查 `runStartTimestampByRunId`，计算 `now - runStartTimestamp`
- 或：直接用 `message.createdAt`（message record 自带）作为起点，`message.end` 的 `timestamp` 作为终点

**简化方案**：用 `message.createdAt`（message record 已有）和最后一个 `part.end` / `message.end` 的 `timestamp` 计算。无需额外 store state。

**显示位置**：消息气泡底部，`PartList` 下方，`TurnTimeline` 上方（或合并进 TurnTimeline summary 行）。只在 agent 消息且 `status !== 'streaming'` 时显示。

**格式**：`本次回答共耗时 45.2s`，用 muted-foreground 小字。

### D6: 时间格式化

**选择**：统一的 `formatDuration(ms)` 工具函数：

```typescript
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const min = Math.floor(ms / 60_000)
  const sec = Math.floor((ms % 60_000) / 1000)
  return `${min}m${sec}s`
}
```

放在 `src/lib/format.ts` 或直接在 `message-parts.tsx` 中（与 `TurnTimeline` 的 `formatDuration` 合并统一）。

**理由**：`TurnTimeline` 已有一个 `formatDuration`，逻辑一致但只处理 `< 1000` 和 `>= 1000` 两档。统一为三档（ms / s / m+s），思考、工具、整轮共用。

### D7: Run 级持久执行指示器

**选择**：在消息列表底部显示一个持久执行指示器组件 `AgentWorkingIndicator`，基于 `useTopLevelRunningRuns` 的 run 级状态（而非 message 级 `status === 'streaming'`），在整个 run 期间持续显示，直到 `run.end` 才移除。

```
消息列表底部:
┌──────────────────────────────────────────────┐
│  🤖 AgentName                                │
│  • • •  正在努力工作...                        │  ← 三个弹跳圆点 + 阶段文本
│  深度思考中 · 5s                              │  ← 当前阶段 + 已运行时长
└──────────────────────────────────────────────┘
```

**组件结构**：

```
MessageList
├── messages.map(...)
└── {hasRunningRuns && <AgentWorkingIndicator runs={runningRuns} />}
```

**阶段推断**：从最近接收的事件类型推断当前执行阶段：

| 最近事件 | 阶段文本 | 说明 |
|----------|---------|------|
| `part.start` (type=thinking) / `part.delta` (thinking.append) | "深度思考中" | Agent 正在推理 |
| `part.start` (type=text) / `part.delta` (text.append) | "生成回答中" | Agent 正在输出文本 |
| `tool.call` | "调用工具: {toolName}" | Agent 正在执行工具 |
| `tool.result` (等待下一个 `message.start`) | "准备下一轮..." | turn 间隙 |
| `message.start` | "正在响应..." | 新消息开始 |

**实现**：store 新增 selector `useRunPhase(conversationId)`，返回 `{ phase: string, toolName?: string }`。内部检查最新 message 的最后一个 part 类型 + `message.status`。

简化方案：不跟踪全局事件历史，而是从 messages 数组推断——取当前 run 的最新 message 的最后一个 part：
- 最后 part 是 `thinking` 且 message streaming → "深度思考中"
- 最后 part 是 `tool_use` 且无对应 `tool_result` → "调用工具: {toolName}"
- 最后 part 是 `text` 且 message streaming → "生成回答中"
- 最后 message `status === 'complete'` 但 run 仍 running → "准备下一轮..."

**Typing indicator 动画**：三个圆点用 CSS `@keyframes bounce` 交替延迟，类似 IM 应用的 "正在输入..." 效果：

```css
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-4px); opacity: 1; }
}
```

三个 `span` 分别 `animation-delay: 0s / 0.15s / 0.3s`。

**已运行时长**：用 `useElapsedTimer(run.startedAt, true)` 显示 run 已运行总时长。

**多 Agent 并行**：群聊中可能有多个 agent 同时在跑。每个 running run 渲染一个 `AgentWorkingIndicator`，显示各自的 avatar + name + 阶段 + 时长。

**备选**：只在输入框上方显示一行文字 "Agent 正在执行..."（不如底部 typing indicator 直观，也不如 IM 体验好）；保持现有 message 级 spinner 不变，只是放大 + 改色（治标不治本，间隙仍会消失）。

### D8: Agent avatar 脉冲环

**选择**：在 `message-item.tsx` 中，当 agent 对应的 run 处于 `running` 状态时，agent avatar 显示脉冲环动画（`ring-2 ring-primary animate-pulse`），替代当前的 `message.status === 'streaming'` 条件。

**当前**：`avatarClassName={cn('transition-all', message.status === 'streaming' && 'ring-2 ring-primary ring-offset-1')}` — 只在 message streaming 时显示环。

**改为**：检查该 message 的 `runId` 对应的 run 是否 `status === 'running'`（用 `runsByConv` 查找），run 活跃时持续显示脉冲环。

**理由**：avatar 脉冲环是 "这个 agent 还活着" 的最直观信号，即使 message 已完成 streaming（turn 间隙），avatar 仍在脉冲，用户知道 agent 还在工作。

## Risks / Trade-offs

- **[旧数据无时间字段]** → 向后兼容：可选字段，旧 parts 无 `startedAt` / `endedAt` 时 UI 不显示时长，不报错
- **[streaming 判断精度]** → 用 `message.status === 'streaming'` + thinking 是最后一个 part 来判断。如果 adapter 在 thinking 后直接发 text part（中间无 part.end），可能导致 thinking 立即折叠。缓解：用 `part.end` 事件作为完成信号更精确，但需要 store 跟踪 `partEndedIndices`
- **[setInterval 性能]** → 每个运行中的 ToolUsePart 启动一个 interval。如果同时有 10+ 个工具在跑，可能有 10 个 interval。缓解：interval 间隔 1s 足够（不是 60fps），且 React 批量更新。实际上一个 message 同时 running 的工具很少超过 5 个
- **[max-height 动画兼容性]** → CSS `max-height` transition 在内容很长时动画可能不流畅。缓解：用 `max-h-40`（160px）作为过渡高度，而非 `max-h-screen`；或用 `grid-template-rows: 1fr / 0fr` 技巧
- **[时钟漂移]** → 后端 `timestamp` 是服务端时间，前端 `now` 是客户端时间。如果两端时钟不同步，运行中实时计时可能跳变。缓解：实时计时用前端 `Date.now()` 差值（纯客户端计时），最终耗时用后端 `endedAt - startedAt`（服务端精确值）
- **[阶段推断不准确]** → 从 messages 数组推断执行阶段依赖最后一个 part 类型，可能在事件间隙推断错误（如 `tool.result` 后 message 还没 start，推断为 "准备下一轮..." 但实际 Agent 已在生成下一个 message 的 thinking）。缓解：阶段文本用 "正在工作..." 这样的泛化文案作为 fallback，不承诺精确阶段
- **[Typing indicator 动画性能]** → 多个 agent 并行时每个 indicator 都有 CSS 动画。缓解：CSS `@keyframes` 动画由浏览器合成线程处理，不触发 layout/paint；同时运行的 agent 很少超过 5 个

## Migration Plan

1. **类型层**：`src/shared/types.ts` + `backend/app/schemas/messages.py` 加可选字段
2. **后端**：`agent_runner.py` 的 `persist_event` 在写入 part dict 时捕获 `timestamp` 到 `startedAt` / `endedAt`
3. **store**：`app-store.ts` reducer 在 `part.start` / `part.end` / `tool.call` / `tool.result` 时捕获 `timestamp`
4. **UI**：重写 `ThinkingPart`，增强 `ToolUsePart` / `ToolCluster`，新增消息底部耗时
5. **UI**：新增 `AgentWorkingIndicator` 组件（typing dots + 阶段文本），插入 `MessageList` 底部；增强 `MessageItem` avatar 脉冲环条件
5. **测试**：store 测试覆盖 timestamp 捕获；UI 快照测试覆盖三态
6. **无需 DB migration**：parts 列已是 JSON，新字段自然嵌入
7. **回滚**：如果需要回滚，UI 组件降级为不显示时长（可选字段天然兜底），类型层加的字段不影响旧代码
