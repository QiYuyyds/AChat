# Proposal: fix-streaming-render-freeze

## Why

当 Agent 流式输出大段内容（项目分析总结、`fs_write` 写大文件）时，前端页面完全卡死、主线程阻塞数十秒无法操作。根因是**每个 `part.delta` 事件都触发对「已累积完整内容」的昂贵渲染**（Shiki 全量语法高亮 / react-markdown 全量 AST 解析），导致 O(N × S) 的重复全量重算——一次 500 行文件写入或 2000 token 总结就能让主线程累计阻塞 30-60 秒。

## What Changes

- **流式期间禁用昂贵渲染（方案 A，前端）**：`TextPart` / `FileWritePreviewPart` / `ThinkingPart` 在 `isStreaming=true` 时走轻量 fallback（纯 `<pre>` 等宽文本 + 自动滚动），不调用 `Markdown` / `CodeBlock`（Shiki）。流式结束（`message.status` 切到 `complete` 或 part 收到 `part.end`）后切回完整渲染。
- **后端 `part.delta` 节流合并（方案 F，后端）**：在 `CustomAdapter`（和 CLI adapter 的 text stream 路径）对 `text.append` / `thinking.append` / `file_write_preview.append` 三类增量 delta 做时间窗合并（默认 50ms 窗口），窗口内的多个 delta 合并为一条 SSE 事件下发，文本拼接后一次性 `part.delta`。保留 `part.start` / `part.end` / `tool.*` / 非 delta 事件的原始语义不受影响。
- **前端 SSE 批处理（方案 B 的最小子集）**：`StreamProvider` 的 `onmessage` 用 `requestAnimationFrame` 合并同一帧内到达的多条事件，一次性 `applyEvent` 循环应用，减少 Zustand `set` 调用次数和 React 渲染轮次。
- **CodeBlock 异步高亮不变更语义**：保持 `CodeBlock` 现有 `useEffect` 异步高亮契约，但因为流式期间不再挂载 `CodeBlock`（用 fallback 替代），高亮只在 part 完成后触发一次。
- **不引入虚拟化**：长对话虚拟化作为独立后续 change 处理，本 change 不触碰 `MessageList` 的渲染结构。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `frontend`: `TextPart` / `FileWritePreviewPart` / `ThinkingPart` 在 streaming 期间使用轻量 fallback 渲染；`StreamProvider` 对 SSE 事件做 rAF 帧合并
- `stream-events`: 新增「delta 合并」语义——adapter 可在时间窗内合并同类 `part.delta` 的 `text` 字段；前端 `StreamProvider` 可在一帧内应用多条事件
- `adapters`: `CustomAdapter`（以及 CLI adapter 的 text streaming 路径）对增量 delta 做 50ms 时间窗合并后再 yield

## Impact

- **前端**：`src/components/message-parts.tsx`（`TextPart` / `ThinkingPart` / `FileWritePreviewPart` 增加 streaming fallback 分支）、`src/components/stream-provider.tsx`（rAF 批处理）、`src/components/markdown.tsx` 和 `src/components/code-block.tsx`（不改动，仅调用方变化）
- **后端**：`backend/app/adapters/custom_adapter.py`（delta 合并器）、`backend/app/adapters/cli_base.py`（如 CLI text streaming 也走合并）、`backend/app/services/agent_runner.py`（consume_stream 不变，合并发生在 adapter 层）
- **Spec 文档**：`specs/02-stream-events.md` 补充 delta 合并语义；`specs/09-frontend-architecture.md` 补充 streaming fallback 策略
- **兼容性**：
  - delta 合并对前后端都是「多产少产」的区别，`part.delta.text` 语义不变（仍是 append），前端 reducer 不改
  - streaming fallback 仅影响渲染层，不改 part 数据结构
  - 旧客户端收到合并后的 delta 仍能正确 append，新客户端收到未合并的 delta 也仍能逐条 apply
  - 无 breaking change
