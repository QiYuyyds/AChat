## Context

Orchestrator 将用户请求拆成 DAG 任务图，按拓扑波次（wave）执行。同一 wave 内的 task 无依赖关系，可并行执行（`asyncio.gather`）。每个 task 对应一个 child run（`childRunIds[taskId] → runId`），child run 的消息通过 `message.runId` 关联。

当前 `MessageList` 按 `messageIdsByConv`（createdAt 时间序）线性渲染所有消息。Orchestrator 并行调度时，多个 agent 的消息按到达时间交错出现，用户难以追踪每个 agent 的独立进度。

前端已有数据：
- `DispatchState.plan: DispatchPlanItem[]` — 每个 item 有 `id`、`agentId`、`dependsOn?: string[]`
- `DispatchState.childRunIds: Record<taskId, runId>` — task 到 child run 的映射
- `MessageRow.runId` — 消息所属的 run
- `RunState.parentRunId` — child run 的 parentRunId 指向 Orchestrator run

这些足以在前端纯计算 wave 分组，无需后端改动。

## Goals / Non-Goals

**Goals:**
- 同一 wave 的并行子 agent 消息横向并排显示，每个 agent 一列
- 不同 wave 之间纵向排列
- 列内消息复用 `group-consecutive-agent-messages` 的分组规则
- 非 dispatch 消息（user、单聊 agent、Orchestrator 自身）保持单列布局
- 窄屏（< 768px）降级为单列纵向堆叠

**Non-Goals:**
- 不改后端、event 协议、DB schema
- 不改 DispatchPlanCard 的渲染（它仍嵌在 Orchestrator message 内）
- 不做列内实时滚动同步（各列独立滚动，由 ScrollArea 统一管理）
- 不处理跨 wave 的列对齐（不同 wave 的列数可能不同，各自独立）

## Decisions

### D1: 消息分段模型

`MessageList` 将 `messages: MessageRow[]` 分为有序的「渲染段」（segments），每个 segment 是以下之一：

```
type Segment =
  | { kind: 'single'; messages: MessageRow[] }          // 单列消息（user / 普通 agent / Orchestrator）
  | { kind: 'wave'; columns: MessageRow[][] }            // 并行 wave，每个子数组是一列
```

分段算法：
1. 遍历 `messages`，维护当前 segment
2. 遇到 Orchestrator message（有对应 `DispatchState`）→ 关闭当前 segment，输出它为 single
3. 遇到 child run message（`runId` 在某 `DispatchState.childRunIds` 的 values 中）：
   - 查找该 child run 对应的 taskId → plan item → wave 层级
   - 同一 wave 的 child messages 归入同一个 `wave` segment 的不同 columns
   - wave 边界变化时关闭当前 wave segment，开新的
4. 其他消息 → 归入 single segment

### D2: Wave 计算（拓扑分层）

```typescript
function computeWaves(plan: DispatchPlanItem[]): Record<taskId, number> {
  const waveOf: Record<string, number> = {}
  for (const item of plan) {
    if (!item.dependsOn || item.dependsOn.length === 0) {
      waveOf[item.id] = 0
    } else {
      waveOf[item.id] = Math.max(...item.dependsOn.map(d => waveOf[d] ?? 0)) + 1
    }
  }
  return waveOf
}
```

遍历 plan 时按拓扑序处理（`dependsOn` 引用的 task 先出现）即可。如果 plan 未排序，先做拓扑排序。

### D3: child run → wave 映射

```
orchestratorRunId → DispatchState
DispatchState.childRunIds: taskId → childRunId
DispatchState.plan: [{ id: taskId, dependsOn: [...] }]
computeWaves(plan): taskId → waveNumber

反向映射：childRunId → taskId → waveNumber
```

`MessageList` 需要一个 `useChildRunWaveMap(conversationId)` hook：
1. 遍历 `dispatchesByRunId`，找到所有有 `plan` 的 dispatch
2. 对每个 dispatch 调 `computeWaves`
3. 通过 `childRunIds` 反映射，得到 `Record<childRunId, { wave: number, taskId: string, orchestratorRunId: string }>`
4. 合并所有 dispatch 的结果

### D4: 渲染布局

```
<div className="space-y-4 p-4">                    ← 纵向排列 segments
  {segments.map(seg =>
    seg.kind === 'single' ? (
      <div>                                          ← 单列
        {seg.messages.map(m => <MessageItem .../>)}
      </div>
    ) : (
      <div className="flex gap-3 max-md:flex-col">   ← 多列并排，窄屏纵向
        {seg.columns.map((col, ci) => (
          <div className="flex-1 min-w-0">            ← 每列等宽
            {col.map(m => <MessageItem .../>)}
          </div>
        ))}
      </div>
    )
  )}
</div>
```

- `flex gap-3`：列间距 12px
- `max-md:flex-col`：窄屏（< 768px）降级为纵向堆叠
- `flex-1 min-w-0`：等分宽度，防止内容溢出
- 列内消息间距复用 `group-consecutive-agent-messages` 的 `mt-0.5` / `mt-4` 逻辑

### D5: Wave segment 内的列排序

同一 wave 内可能有多个 task，列的顺序按 `plan` 数组中 task 的出现顺序排列（用户在 DispatchPlanCard 看到的顺序一致）。

### D6: 流式时的动态分组

流式过程中新 `message.start` 事件带来新消息，`MessageList` 每次 re-render 重新计算 segments。新消息的 `runId` 决定它属于哪一列。如果新消息的 runId 不属于任何已知 child run（如 Orchestrator 自己的新消息），它归入 single segment。

### D7: 列头标识

每列顶部显示一个紧凑的 agent 标识（头像 + 名字 + task id），让用户知道这列是谁在做什么。列内首条消息不再重复显示头像（类似 grouped=true），但保留 token badge。

```
┌─────────────────────┬─────────────────────┐
│ 🤖 Claude  t1       │ 🧠 DeepSeek  t2     │  ← 列头
│                     │                     │
│ [thinking] 分析...  │ [thinking] 分析...  │
│              1.2k tok│              0.8k tok│
│                     │                     │
│ [🔧 fs_read: ...]   │ [🔧 bash: ...]     │
│              1.5k tok│              1.1k tok│
│                     │                     │
│ 前端分析完成...      │ 后端分析完成...      │
│              2.0k tok│              1.8k tok│
│ [2 turns · 4.7k tok]│ [2 turns · 3.7k tok]│
└─────────────────────┴─────────────────────┘
```

## Risks / Trade-offs

- **[复杂度] 消息分段算法** → 纯前端计算，O(n) 遍历消息 + O(plan) 拓扑分层。消息量大时（1000+）可能有性能问题，但当前单会话消息量远不到这个量级。可接受。
- **[风险] child run 消息时间交错导致列内消息不连续** → 同一列（同一 runId）的消息按 createdAt 排序自然连续，不会交错。分段算法按消息遍历顺序归列，同 runId 的消息天然聚集。
- **[风险] 列数过多（4+ task 并行）** → 每列过窄不可读。`max-md:flex-col` 在窄屏降级；宽屏 4 列时每列约 300px，勉强可读。超过 4 列时可考虑横向滚动，但暂不实现（当前 DAG wave 内并行度通常 2-3）。
- **[取舍] 列头 vs 每条消息显示头像** → 列头更简洁，且列内消息都是同一 agent，不需要重复头像。代价是列头占一行空间，但收益是列内消息更紧凑。
- **[风险] 撤回/编辑后消息列表变化** → segments 重新计算，自动正确。无边界问题。
- **[依赖] 需要先完成 `group-consecutive-agent-messages`** → 列内分组复用其 `grouped` prop 和 `MessageItem` 的条件渲染逻辑。
