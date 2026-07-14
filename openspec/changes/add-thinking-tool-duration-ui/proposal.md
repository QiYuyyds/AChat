## Why

当前 Agent 对话的前端体验缺少关键的时间反馈和执行状态感知：思考内容默认折叠（用户看不到 AI 正在推理什么），工具调用不显示耗时，用户无法感知 Agent 的执行节奏。更关键的是，ReAct 循环中 `message.end` 后到下一条 `message.start` 之间有间歇（Agent 在调用工具、等待结果、准备下一轮），此时 message 级 spinner 消失但 run 仍在进行，用户误以为 Agent 挂了或完成了。后端的 `part.start`/`part.end`/`tool.call`/`tool.result` 事件已携带 `timestamp`，但前端 store 完全未捕获；store 已有 run 级别 `status: 'running'` 数据（`runsByConv`），但消息列表未用于视觉反馈。

## What Changes

- **thinking part 加时间字段**：`MessagePart` 的 `thinking` 类型新增可选字段 `startedAt` / `endedAt`（Unix epoch ms），由 store reducer 从 `part.start` / `part.end` 事件的 `timestamp` 捕获
- **tool_use part 加 `startedAt`**：从 `tool.call` 事件的 `timestamp` 捕获
- **tool_result part 加 `endedAt`**：从 `tool.result` 事件的 `timestamp` 捕获
- **思考三态 UI**：流式中 → 不折叠 + "深度思考中" 指示 + 限制可见高度自动滚动；完成 → 完全折叠（0 行内容）+ 显示耗时 + 平滑折叠动画；展开 → 用户可手动展开查看
- **工具耗时显示**：完成态显示最终耗时；运行中实时计时（`setInterval` 显示已运行秒数）
- **ToolCluster 总耗时**：展开后内部各 ToolUsePart 显示各自耗时，cluster header 显示总耗时
- **整轮回答总耗时**：`message.end` 时在消息气泡底部显示 "本次回答共耗时 Xs"
- **持久化 Agent 执行指示器**：在消息列表底部显示 run 级别的持久执行指示器（非 message 级别），ReAct 循环中 turn 间隙也不消失。包含动画 typing indicator + 当前执行阶段文本（"深度思考中" / "调用工具: bash" / "生成回答中"），基于已有的 `useTopLevelRunningRuns` 数据

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `message-parts`: thinking / tool_use / tool_result 类型新增可选时间字段（`startedAt` / `endedAt`），用于持久化和展示执行时长
- `frontend`: ThinkingPart 三态行为（streaming-open / completed-collapsed / user-expanded）；ToolUsePart 运行中实时计时 + 完成耗时；ToolCluster 总耗时；消息气泡底部整轮耗时显示；消息列表底部 run 级持久执行指示器（typing indicator + 阶段文本）

## Impact

- **共享类型**：`src/shared/types.ts` — `thinking` / `tool_use` / `tool_result` MessagePart 类型加可选字段
- **后端 schema**：`backend/app/schemas/messages.py` — Pydantic MessagePart 模型加可选字段；`backend/app/services/agent_runner.py` — `persist_event` 在写入 part dict 时捕获 `timestamp`
- **前端 store**：`src/stores/app-store.ts` — `part.start` / `part.end` / `tool.call` / `tool.result` reducer 捕获 timestamp 到对应 part 字段
- **前端 UI**：`src/components/message-parts.tsx` — ThinkingPart 重写三态逻辑 + 动画；ToolUsePart 加计时；ToolCluster 加总耗时
- **前端 UI**：`src/components/message-item.tsx` — 消息底部整轮耗时显示；agent avatar 在 run 活跃时显示脉冲环（不只是 message streaming 时）
- **前端 UI**：`src/components/message-list.tsx` 或新组件 — 消息列表底部 run 级持久执行指示器（typing dots + 阶段文本）
- **前端 store**：`src/stores/app-store.ts` — 新增 selector 提取当前 run 的执行阶段（thinking / tool_call / text_generation），基于最近事件类型推断
- **前端测试**：`src/stores/app-store.test.ts` — 更新 part / tool 事件的 timestamp 捕获测试
- **向后兼容**：所有新增字段为可选（`?`），旧数据无时间字段时 UI 降级为不显示时长，不影响已有消息渲染
- **无破坏性变更**：不改事件协议（`StreamEvent` 类型不变），不改 DB schema（parts 列仍是 JSON），不改 API 路由
