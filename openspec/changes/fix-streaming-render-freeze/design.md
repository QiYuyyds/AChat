# Design: fix-streaming-render-freeze

## Context

AChat 前端在 Agent 流式输出大段内容时完全卡死，主线程阻塞数十秒无法操作。两个典型场景：

1. **Agent 分析项目的总结阶段**：CustomAdapter 产出大量 `text.append` delta（每 delta 几十字符），累积成数 KB 的 markdown 总结。前端 `TextPart` 用 `react-markdown` + `remark-gfm` 渲染，**每个 delta 都触发对完整累积文本的 AST 重新解析**。
2. **Agent 写入大文件**（如 `backend/test_grades.py`）：CustomAdapter 的 `_ContentExtractor` 从 partial `args_buffer` 提取 `content` 字段，产出大量 `file_write_preview.append` delta。前端 `FileWritePreviewPart` → `CodeBlock` → **每个 delta 都触发 Shiki 对完整累积内容的 TextMate 语法高亮**。

两个场景的卡顿根因都是 **O(N × S)** 重复全量重算：N = delta 数量（数百到数千），S = 最终内容大小（数 KB 到数十 KB）。每次重算 20-200ms，累计阻塞 30-60 秒。

后端 `EventBus.publish` 同步、无批处理；前端 `StreamProvider.onmessage` 每个 SSE 消息立即 `applyEvent`，无 rAF 合并。这放大了上面的 O(N × S) 问题——每个小 delta 都触发一次完整的 React 渲染轮次。

## Goals / Non-Goals

**Goals:**

- 消除流式输出时的前端卡死：流式期间主线程单次任务 < 50ms（60fps 友好）
- 保留流式结束后的高质量渲染（Shiki 语法高亮、markdown 排版）
- 后端 delta 合并对前端透明：`part.delta` 语义不变（仍是 append），前端 reducer 无需修改
- 前端 SSE 批处理对 store reducer 透明：`applyEvent` 签名不变，只是调用频率降低
- 降级安全：任何 fallback 渲染异常不影响数据正确性，part 内容仍完整存入 store

**Non-Goals:**

- 不做 `MessageList` 虚拟化（长对话 > 200 条消息的卡顿是独立问题，后续 change 处理）
- 不改 `part.delta` 的事件 schema（`text` / `thinking` / `code` / `file_write_preview.append` 的字段不变）
- 不改 `MessagePart` 类型定义
- 不改后端 `EventBus` 的 queue 语义（仍是 `put_nowait` + oldest-drop overflow）
- 不覆盖 CLI Agent（Claude Code / Codex）的 tool_call 流式预览——CLI 走子进程事件翻译，不在本 change 范围。但 CLI 的 text streaming 如果也走 `part.delta`，会受益于方案 F 的合并器
- 不做「流式期间增量高亮」（只高亮新增行）——复杂度高、Shiki 不原生支持，留给后续

## Decisions

### D1: 流式期间用纯 `<pre>` fallback，结束后切回完整渲染（方案 A）

**选择**：`TextPart` / `FileWritePreviewPart` / `ThinkingPart` 在 `isStreaming=true` 时渲染轻量 fallback：

| Part 类型 | Streaming fallback | Complete 渲染 |
|---|---|---|
| `TextPart` | `<pre className="whitespace-pre-wrap break-words">` 纯文本 + 自动滚动 | `<Markdown>` (react-markdown + remark-gfm) |
| `FileWritePreviewPart` | `<pre>` 纯文本 + 闪烁光标 + 自动滚动 | `<CodeBlock>` (Shiki) 或 `<DiffBlock>` |
| `ThinkingPart` | 已有 streaming 分支（纯 `<pre>` 风格），保持不变 | 已有 collapsed/expanded 分支，保持不变 |

`isStreaming` 的判定逻辑：`messageStatus === 'streaming' && partIndex === lastContentPartIndex`（已在 `PartList` 中计算，复用）。

切换时机：当 `message.status` 从 `streaming` 切到 `complete`（或 `error` / `aborted`），`PartList` 重新计算 `isStreaming=false`，fallback 组件卸载、完整渲染组件挂载。**这是单向切换**——一旦进入 complete 态，不会再回到 streaming。

**备选**：
- A) 给 `CodeBlock` / `Markdown` 加 `debounce`，流式期间延迟渲染 → 仍会在 debounce 窗口内做全量重算，只是频率降低；且 debounce 期间用户看不到内容更新，体感差
- B) 增量高亮（只高亮新增行） → Shiki 不原生支持增量，需要自己维护行级 diff + 高亮缓存，复杂度高
- C) 虚拟化 `MessageList` → 解决不了单条消息内 O(N × S) 的问题，单条大消息仍卡

**理由**：fallback 是最简单、最可靠的方案。流式期间用户的主要诉求是「看到内容在生长」，纯 `<pre>` 已满足。语法高亮 / markdown 排版是「锦上添花」，留到完成后一次性渲染即可。切换时会有一次「闪烁」（纯文本 → 高亮），但远好于卡死。

### D2: 后端 delta 时间窗合并，50ms 窗口（方案 F）

**选择**：在 `CustomAdapter` 的 streaming 循环里，对 `text.append` / `thinking.append` / `file_write_preview.append` 三类增量 delta 做合并。用一个 `_DeltaFlusher` 辅助类：

```python
class _DeltaFlusher:
    """Accumulates same-key deltas within a time window, flushes as one event."""
    def __init__(self, window_ms: int = 50): ...
    def feed(self, part_index: int, delta_type: str, text: str) -> PartDeltaEvent | None:
        """Feed a delta. Returns a merged event if window elapsed, else None."""
    def flush(self) -> list[PartDeltaEvent]:
        """Flush remaining buffered deltas (call on part.end / turn end)."""
```

合并规则：
- 同一 `(message_id, part_index, delta_type)` 的 delta 在 50ms 窗口内累积 `text` 字段
- 窗口结束（超时 OR 收到 `part.end` OR 收到非 delta 事件 OR turn 结束）时，发一条合并后的 `PartDeltaEvent`，`text` 是窗口内所有 delta 的拼接
- 不同 `(part_index, delta_type)` 的 delta 独立累积、独立 flush
- 非 delta 事件（`part.start` / `part.end` / `tool.call` / `tool.result` 等）**不合并**，直接 yield，但在 yield 前先 flush 所有 pending delta（保证事件顺序：delta → 非 delta）

**时间窗选择 50ms 的理由**：
- 人眼对 < 100ms 的延迟几乎无感（60fps = 16.7ms/帧，100ms ≈ 6 帧）
- 50ms 窗口能合并 5-20 个 LLM streaming chunk（OpenAI stream chunk 间隔通常 10-30ms）
- 不会让用户感觉「内容卡顿不更新」——50ms 后必 flush

**备选**：
- A) 按字符数合并（累积到 N 字符才 flush） → 字符数难以统一调参，大文件和小文本体验不一致
- B) 在 `EventBus` 层合并 → 修改 `EventBus` 影响所有事件类型，风险高；且 `EventBus` 是 pub-sub 模型，合并逻辑放在订阅端不合适
- C) 在 `consume_stream` 层合并 → `consume_stream` 已经很复杂，加合并逻辑会混入业务逻辑；合并应该在 adapter 产出事件时做，保持「adapter 产什么、consume_stream 消费什么」的清晰边界

**理由**：合并器放在 adapter 层，对下游完全透明——`consume_stream` 和 `EventBus` 不需要改动。合并的是「同类 delta 的 text 字段拼接」，`part.delta` 的 schema 和 reducer 语义完全不变。

### D3: 前端 SSE rAF 帧合并（方案 B 最小子集）

**选择**：`StreamProvider.onmessage` 不再直接调用 `applyEvent`，而是把事件推入一个 `pendingEvents` 数组，用 `requestAnimationFrame` 在下一帧统一 flush：

```typescript
const pendingRef = useRef<StreamEvent[]>([])
const rafRef = useRef<number | null>(null)

const scheduleFlush = () => {
  if (rafRef.current !== null) return
  rafRef.current = requestAnimationFrame(() => {
    rafRef.current = null
    const events = pendingRef.current
    pendingRef.current = []
    for (const e of events) applyEvent(e)
  })
}

activeSource.onmessage = (e) => {
  // ... parse ...
  pendingRef.current.push(parsed)
  scheduleFlush()
}
```

**关键约束**：
- `heartbeat` 和 `connected` 事件**立即处理**，不入队（它们不影响渲染，且需要即时反映连接状态）
- flush 是同步循环 `applyEvent`，多个事件在一个 Zustand `set` 批次内应用——Immer + Zustand 支持这种模式（多次 `set` 会被 React 18+ 自动批处理）
- 组件卸载时 cancel rAF 并 flush 剩余事件

**备选**：
- A) 用 `setTimeout(0)` 替代 rAF → setTimeout 最小延迟 4ms，且不与浏览器渲染对齐，可能丢帧
- B) 在 `applyEvent` 内部批处理 → 需要改 store reducer 签名，影响面大
- C) 不做前端批处理，只靠后端合并 → 后端 50ms 合并能降低 5-10x 事件数，但前端仍可能在一帧内收到多条 SSE 消息（网络突发），rAF 合并能兜底

**理由**：rAF 合并对 `applyEvent` 完全透明，不改变 reducer 契约。与后端合并叠加效果最好：后端把 1000 个 delta 压到 100 个，前端 rAF 再把 100 个压到 ~10 帧渲染轮次。

### D4: fallback 期间的自动滚动

**选择**：`FileWritePreviewPart` 和 `ThinkingPart` 的 fallback 复用现有的 `scrollRef.current.scrollTop = scrollRef.current.scrollHeight` 自动滚动逻辑（已有，保持不变）。`TextPart` fallback 不单独做滚动——外层 `MessageList` 的 `scheduleScrollToBottom` 已经会在 `lastMessageContentLength` 变化时触发节流滚动（80ms）。

**理由**：避免在 `TextPart` 内引入新的滚动逻辑，复用 `MessageList` 的全局 sticky-bottom 机制。`FileWritePreviewPart` 是独立卡片，需要自己的滚动容器（已有）。

### D5: fallback 的样式

**选择**：fallback 用与 complete 渲染一致的容器样式（padding / border / 字体），只把内容部分换成 `<pre>`。这样切换时只有内容区域「闪烁」，容器尺寸和位置稳定，避免布局抖动（layout shift）。

```tsx
// TextPart fallback
<div className="text-sm leading-6 text-foreground">
  <pre className="whitespace-pre-wrap break-words font-sans">
    {content}
  </pre>
</div>

// FileWritePreviewPart fallback（复用现有 Card 容器，只替换内容区）
<Card className="overflow-hidden border-primary/20 py-0">
  <div className="flex items-center gap-2 bg-primary/5 px-3 py-1.5 text-xs">
    {/* 现有 header：文件名 + Loader2 + "生成中" */}
  </div>
  <div ref={scrollRef} className="max-h-[24rem] overflow-auto">
    <pre className="px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
      {content}
    </pre>
    <span className="absolute bottom-1 right-2 size-2 animate-pulse rounded-full bg-green-500" />
  </div>
</Card>
```

**理由**：`font-sans` 让 fallback 的文本字体与 markdown 正文一致（不是等宽），避免体感突兀。`FileWritePreviewPart` 用 `font-mono` 因为代码本就该等宽。

## Risks / Trade-offs

- **[Risk] 切换闪烁** → Mitigation：fallback 容器样式与 complete 一致，只有内容区变化；切换只在 streaming→complete 时发生一次，可接受
- **[Risk] 50ms 合并窗口内 LLM 突发输出被延迟] → Mitigation：50ms 是上限不是固定延迟，窗口内第一个 delta 立即开始计时，超时即 flush；用户对 50ms 延迟无感
- **[Risk] rAF 在后台 tab 被节流（1Hz）] → Mitigation：后台 tab 时 SSE 事件本就不需要实时渲染；`document.hidden` 时可降级为立即 apply（可选优化，初版不做）
- **[Risk] fallback 期间用户复制不到高亮文本] → Mitigation：`<pre>` 里的纯文本本身可复制；高亮只是视觉，不影响数据
- **[Trade-off] 流式期间无 markdown 排版（列表/标题/表格都显示为纯文本）] → 这是可接受的代价：用户在流式时主要看「内容在生长」，排版是完成后的阅读体验
- **[Trade-off] 50ms 合并让「打字机」效果略不流畅] → 50ms 内的多个 chunk 合并为一次更新，视觉上从「逐字」变成「逐词/逐句」，但远好于卡死

## Migration Plan

**部署顺序**（无 breaking change，可独立部署）：

1. **先部署前端**（方案 A + B）：前端改动对后端无依赖，立即生效。即使后端不合并，前端 fallback 也能消除单条消息的 O(N × S) 卡顿。
2. **再部署后端**（方案 F）：后端合并对前端透明，部署后前端事件数降低 5-10x，进一步减少 rAF 合并压力。

**回滚策略**：
- 前端：revert `message-parts.tsx` 和 `stream-provider.tsx` 改动，回到原渲染逻辑（会重新卡死，但功能正常）
- 后端：revert `custom_adapter.py` 的 `_DeltaFlusher`，回到逐 delta yield（前端仍能正确处理，只是事件数变多）

**验证标准**：
- 流式输出 2000 token markdown 总结：主线程单次任务 < 50ms，无 > 100ms 的长任务
- 流式写入 500 行 Python 文件：主线程单次任务 < 50ms
- 流式结束后 1 秒内完成 Shiki 高亮 / markdown 渲染
- 长对话（50 条消息）滚动无明显卡顿（虚拟化不在本 change 范围，仅验证不回归）

## Open Questions

1. **CLI Agent（Claude Code / Codex）的 text streaming 是否也接入 `_DeltaFlusher`？** CLI adapter 走子进程 stdout 翻译，text delta 的产出模式与 CustomAdapter 不同。初版建议先只做 CustomAdapter，CLI 作为后续 task。如果 CLI 的 text delta 也导致卡顿，可以复用同一个 `_DeltaFlusher` 类。
2. **`thinking.append` 是否需要合并？** Thinking 内容通常较短（几百字），卡顿风险低。但为了对称性和一致性，建议一并合并。如果 thinking 有特殊的实时性要求（用户想看到思考过程的逐字更新），可以排除 thinking 只合并 text 和 file_write_preview。
3. **rAF 合并在 `document.hidden` 时是否降级？** 后台 tab 时 rAF 被节流到 1Hz，事件会大量累积在 `pendingRef`。初版可以不处理（最多一次性 apply 一批事件），后续可加 `visibilitychange` 监听降级为立即 apply。
