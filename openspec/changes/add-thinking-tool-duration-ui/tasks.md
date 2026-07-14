## 1. 类型层 — MessagePart 加时间字段

- [x] 1.1 在 `src/shared/types.ts` 中给 `thinking` part 类型加 `startedAt?: number` / `endedAt?: number`
- [x] 1.2 在 `src/shared/types.ts` 中给 `tool_use` part 类型加 `startedAt?: number`
- [x] 1.3 在 `src/shared/types.ts` 中给 `tool_result` part 类型加 `endedAt?: number`
- [x] 1.4 在 `src/stores/app-store.ts` 的 `areMessagePartsEquivalent` 函数中更新 thinking / tool_use / tool_result 的比较逻辑以包含新字段
- [x] 1.5 运行 `pnpm typecheck` 验证类型变更无破坏

## 2. 后端 — persist_event 捕获时间戳

- [x] 2.1 在 `backend/app/schemas/messages.py` 中给 thinking / tool_use / tool_result 的 Pydantic 模型加可选时间字段
- [x] 2.2 在 `backend/app/services/agent_runner.py` 的 `persist_event` 函数中，处理 `part.start` 事件时将 `event.timestamp` 写入 part dict 的 `startedAt` 字段
- [x] 2.3 在 `persist_event` 中处理 `part.end` 事件时将 `event.timestamp` 写入对应 part dict 的 `endedAt` 字段
- [x] 2.4 在 `persist_event` 中处理 `tool.call` 事件时将 `event.timestamp` 写入 tool_use part dict 的 `startedAt` 字段
- [x] 2.5 在 `persist_event` 中处理 `tool.result` 事件时将 `event.timestamp` 写入 tool_result part dict 的 `endedAt` 字段
- [x] 2.6 运行 `ruff check .` 和 `pytest` 验证后端变更

## 3. 前端 Store — reducer 捕获 timestamp

- [x] 3.1 在 `src/stores/app-store.ts` 的 `part.start` case 中，将 `event.timestamp` 写入 `msg.parts[event.partIndex].startedAt`（仅对 thinking / text / code part 类型）
- [x] 3.2 在 `part.end` case 中（当前为 no-op），将 `event.timestamp` 写入 `msg.parts[event.partIndex].endedAt`（仅对 thinking part）
- [x] 3.3 在 `tool.call` case 中，将 `event.timestamp` 写入新 push 的 `tool_use` part 的 `startedAt`
- [x] 3.4 在 `tool.result` case 中，将 `event.timestamp` 写入 `tool_result` part 的 `endedAt`
- [x] 3.5 在 `src/stores/app-store.test.ts` 中新增测试：part.start 捕获 startedAt、part.end 捕获 endedAt、tool.call 捕获 startedAt、tool.result 捕获 endedAt
- [x] 3.6 运行 `pnpm test` 验证 store 测试通过

## 4. 前端 UI — formatDuration 工具函数

- [x] 4.1 在 `src/lib/format.ts`（或合适位置）新建/统一 `formatDuration(ms: number): string` 函数，支持 ms / s / m+s 三档格式
- [x] 4.2 将 `src/components/turn-timeline.tsx` 中的局部 `formatDuration` 替换为引用统一函数
- [x] 4.3 运行 `pnpm typecheck` 验证

## 5. 前端 UI — ThinkingPart 三态重写

- [x] 5.1 重写 `ThinkingPart` 组件：接收 `content`、`startedAt?`、`endedAt?`、以及 `isStreaming` prop（从 `message.status === 'streaming'` + part 是最后一个 content part 推断）
- [x] 5.2 实现 streaming-open 态：不折叠、显示 "深度思考中..." 标签、内容区 `max-h-40 overflow-y-auto`、`useRef` + `useEffect` 自动滚动到底
- [x] 5.3 实现 completed-collapsed 态：完全折叠（0 行内容）、显示 "已深度思考 · {duration}" 标签、提供 "展开" 按钮
- [x] 5.4 实现 user-expanded 态：显示完整内容 + duration 标签 + "收起" 按钮
- [x] 5.5 实现从 streaming-open 到 completed-collapsed 的平滑 CSS 动画（max-height + opacity transition）
- [x] 5.6 处理无 `startedAt` / `endedAt` 的降级：completed-collapsed 态显示 "已深度思考"（无 duration）
- [x] 5.7 更新 `PartList` / `PartRenderer` 中 ThinkingPart 的调用，传入 `startedAt` / `endedAt` / `isStreaming` 参数
- [x] 5.8 运行 `pnpm typecheck` 和 `pnpm lint` 验证

## 6. 前端 UI — ToolUsePart 计时

- [x] 6.1 创建 `useElapsedTimer(startedAt: number | undefined, isActive: boolean)` 自定义 hook，返回已运行毫秒数，内部用 `setInterval(1000)` + `useEffect` 清理
- [x] 6.2 在 `ToolUsePart` 的 `running` 态使用 `useElapsedTimer`，header 显示 "调用中 · {elapsed}s..."
- [x] 6.3 在 `ToolUsePart` 的 `success` / `error` 态，用 `completion` 的 `endedAt` 和 part 的 `startedAt` 计算 duration，header 显示 "已完成 · {duration}" / "失败 · {duration}"
- [x] 6.4 处理无 timing 数据的降级：running 态显示 "调用中"（无计时），completed 态显示状态标签（无 duration）
- [x] 6.5 需要将 `tool_use` part 的 `startedAt` 和 `tool_result` part 的 `endedAt` 传入 `ToolUsePart`（通过 `PartList` 的 `resultByCallId` 扩展或直接传参）

## 7. 前端 UI — ToolCluster 总耗时

- [x] 7.1 在 `ToolCluster` 中计算 `totalDuration = max(endedAt) - min(startedAt)`，header 显示总耗时
- [x] 7.2 当 cluster 中有 running 工具时，用最早的 `startedAt` 启动 `useElapsedTimer`，header 显示 "X 进行中 · Ys..."
- [x] 7.3 处理无 timing 数据的降级：不显示总耗时

## 8. 前端 UI — 消息底部整轮耗时

- [x] 8.1 在 `message-item.tsx` 中，当 `message.status !== 'streaming'` 且 message role 为 agent 时，计算整轮耗时（earliest `startedAt` → latest `endedAt` across all parts，或 fallback 到 `message.createdAt` → 最后 part 的 timestamp）
- [x] 8.2 在 `PartList` 下方、`TurnTimeline` 上方渲染 "本次回答共耗时 {duration}"（muted-foreground 小字）
- [x] 8.3 处理无 timing 数据的降级：不显示整轮耗时

## 9. 前端 UI — Run 级持久执行指示器

- [x] 9.1 在 `src/stores/app-store.ts` 中新增 selector `useRunPhase(conversationId, runId)`，返回 `{ phase: string, toolName?: string }`。从该 run 的最新 message 的最后一个 part 类型 + `message.status` 推断阶段（thinking → "深度思考中"；tool_use 无 result → "调用工具: {name}"；text streaming → "生成回答中"；message complete 但 run running → "准备下一轮..."；fallback → "正在工作..."）
- [x] 9.2 创建 `src/components/agent-working-indicator.tsx` 组件：接收 `run` (RunState) + `conversationId`，渲染 agent avatar + name + 三个弹跳圆点 + 阶段文本 + `useElapsedTimer(run.startedAt, true)` 已运行时长
- [x] 9.3 实现 typing dots 动画：三个 `span` 圆点用 CSS `@keyframes typing-bounce`，分别 `animation-delay: 0s / 0.15s / 0.3s`
- [x] 9.4 在 `src/components/message-list.tsx` 中，`segments.map(...)` 之后渲染 `useTopLevelRunningRuns(conversationId)` 的 running runs，每个渲染一个 `AgentWorkingIndicator`
- [x] 9.5 处理多 agent 并行：群聊中多个 run 同时 running 时，每个渲染独立 indicator

## 10. 前端 UI — Agent avatar 脉冲环

- [x] 10.1 在 `src/stores/app-store.ts` 中新增 selector `useIsRunActive(conversationId, runId)`，返回 boolean（run 存在且 `status === 'running'`）
- [x] 10.2 在 `src/components/message-item.tsx` 中，将 avatar 脉冲环条件从 `message.status === 'streaming'` 改为 `useIsRunActive(message.conversationId, message.runId)`（run 活跃时持续脉冲，不只 message streaming 时）
- [x] 10.3 将原有 message streaming 时的 `Loader2` spinner 保留（作为 message 级细粒度指示），但降级为更小 / 更低调（`size-2.5 text-muted-foreground/50`），主要视觉信号交给 avatar 脉冲环 + 底部 indicator

## 11. 测试与验证

- [x] 11.1 前端 store 测试：验证 `part.start` / `part.end` / `tool.call` / `tool.result` 正确捕获 timestamp
- [x] 11.2 前端 lint + typecheck：`pnpm lint` + `pnpm typecheck` 全部通过
- [x] 11.3 后端 lint + 测试：`ruff check .` + `pytest` 全部通过
- [ ] 11.4 手动验证：发起一个 agent 对话，观察思考流式展开 → 完成自动折叠 + 时长；工具调用运行中计时 → 完成耗时；多工具 cluster 总耗时；消息底部整轮耗时
- [ ] 11.5 手动验证 run 级指示器：ReAct 循环中观察 message.end 后底部 indicator 不消失 + avatar 脉冲环持续 + 阶段文本正确切换；run.end 后 indicator 消失 + avatar 停止脉冲
- [ ] 11.6 历史消息验证：刷新页面，确认历史消息的思考/工具时长仍从 DB 数据中正确显示
