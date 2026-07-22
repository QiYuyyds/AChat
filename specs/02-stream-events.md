# Spec 02 — StreamEvent 协议

> 整个系统的「腰部」。L2 Adapter 产生事件 → L3 路由 → L4 SSE 推 → L5 store reducer 应用。**任何新 Adapter / UI 组件都必须围绕这套事件展开。**

---

## 设计原则

1. **细粒度**：run / message / part / tool / artifact / dispatch 各自有事件
2. **增量**：流式 part 用 `delta` 事件追加，不重传全量
3. **可恢复**：所有事件携带稳定 ID，前端崩溃重连后可补对账
4. **传输无关**：事件本身是纯数据，不绑死 SSE。未来换 WebSocket / RSC streaming 不影响事件定义

---

## 事件类型全集

```typescript
// 所有事件的公共字段
interface BaseEvent {
  conversationId: string        // 必带，前端按此分发到对应会话桶
  timestamp: number             // unix ms，仅用于诊断
}

type StreamEvent = BaseEvent & (
  // —— Run 生命周期 ——
  | { type: 'run.start',    runId: string, agentId: string, triggerMessageId: string, parentRunId?: string }
  | { type: 'run.end',      runId: string, status: 'complete' | 'failed' | 'aborted', error?: string }

  // —— Message 生命周期 ——
  | { type: 'message.start', messageId: string, agentId: string, runId: string }
  | { type: 'message.end',   messageId: string }
  | { type: 'message.added', message: Message }   // 用户消息：已落库后广播，让非发送方客户端实时插入
  | { type: 'message.removed', messageIds: string[], artifactIds: string[] }  // 撤回/编辑/重新生成删除消息后广播

  // —— Part 增量（核心，最高频）——
  | { type: 'part.start',  messageId: string, partIndex: number, part: MessagePart }
  | { type: 'part.delta',  messageId: string, partIndex: number, delta: PartDelta }
  | { type: 'part.end',    messageId: string, partIndex: number }

  // —— 工具调用 ——
  | { type: 'tool.call',   messageId: string, callId: string, toolName: string, args: any }
  | { type: 'tool.result', messageId: string, callId: string, result: any, isError: boolean }

  // —— 产物 ——
  | { type: 'artifact.create', artifact: Artifact }
  | { type: 'artifact.update', artifactId: string, patch: Partial<ArtifactContent> }
  | { type: 'deploy.status', messageId: string, deployment: DeployStatusRecord }

  // —— Orchestrator 调度可视化 ——
  | { type: 'dispatch.plan',  runId: string, plan: DispatchPlanItem[] }
  | { type: 'dispatch.start', parentRunId: string, childRunId: string, taskId: string, agentId: string }
  | { type: 'dispatch.end',
      parentRunId: string,
      childRunId?: string,
      taskId: string,
      status: 'complete' | 'failed' | 'aborted' | 'skipped',
      error?: string }

  // —— Agent fs_write 审批（仅 review 模式发；详见 Spec 07） ——
  | { type: 'fs_write.pending',  pendingWrite: PendingWrite }       // agent 调 fs_write，等用户审批
  | { type: 'fs_write.resolved', pendingId: string, applied: boolean } // approve / reject / run abort

  // —— Agent bash 关键命令审批（详见 Spec 07） ——
  | { type: 'bash_command.pending',  pendingCommand: PendingBashCommand }
  | { type: 'bash_command.resolved', pendingId: string, approved: boolean }

  // —— Token usage 计量（adapter 在 run 结束前 emit）——
  | { type: 'run.usage', runId: string, usage: RunUsageEvent }

  // —— 心跳 ——
  | { type: 'heartbeat' }

  // —— Guide Agent 管理操作副作用通知 ——
  | { type: 'guide_side_effect', target: 'agents' | 'skills' | 'mcp' | 'documents' | 'memory' | 'profile' | 'conversations', action: 'create' | 'update' | 'delete' | 'refresh', payload?: unknown }

  // —— 执行计划（solo + coordinated mode Agent 的结构化进度追踪）——
  | { type: 'plan.created',  planId: string, steps: PlanStep[], complexity: 'simple' | 'moderate' | 'complex' }
  | { type: 'plan.step_update', planId: string, steps: PlanStep[] }

// —— 文件写入预览（SDK Agent fs_write 流式预览 + 执行完成后 diff 回填）——
| { type: 'file_write_preview.complete', messageId: string, callId: string, path: string, oldContent: string | null, newContent: string | null, status: 'complete' | 'failed' }
)

interface RunUsageEvent {
  inputTokens: number
  outputTokens: number
  cacheCreationTokens: number    // Anthropic prompt cache 写入
  cacheReadTokens: number        // Anthropic prompt cache 命中；DeepSeek prompt_cache_hit_tokens 也映射到这
  lastInputTokens?: number       // 最近一次 input prompt 长度，UI 用作 ctx 仪表
  model?: string                 // 实际使用的模型 id
}

interface PendingWrite {
  id: string                    // pwr_<nanoid>
  conversationId: string
  agentId: string
  runId: string
  path: string                  // workspace 相对路径
  absolutePath: string
  oldContent: string | null     // null 表示新建文件
  newContent: string
  createdAt: number
}

interface PendingBashCommand {
  id: string                    // pbc_<nanoid>
  conversationId: string
  agentId: string
  runId: string
  command: string
  cwd: string
  reason: string
  createdAt: number
}

interface DispatchPlanItem {
  id: string                    // 子任务 id，如 't1'
  agentId: string
  task: string                  // 给该 agent 的具体任务描述
  dependsOn: string[]           // 前置任务 id 列表
  planStepId?: string          // 可选：关联到 create_plan 中的步骤 id，用于自动更新 plan step 状态
}

type PartDelta =
  | { type: 'text.append',     text: string }
  | { type: 'code.append',     text: string }
  | { type: 'thinking.append', text: string }
  // 注：tool_use / tool_result / artifact_ref / execution_plan 不增量，整体替换或独立事件

interface PlanStep {
  id: string
  title: string
  status: 'pending' | 'in_progress' | 'done' | 'failed' | 'skipped'
}
```

---

## Delta 合并语义（Coalescing）

Adapter **可以**在时间窗内合并同 key 的 `part.delta` 事件，减少 SSE 事件量。合并对下游完全透明：`part.delta` 的 schema 不变，`text` 仍是 append 语义，前端 reducer 无需修改。

### 合并规则

- **合并 key**：`(message_id, part_index, delta.type)`。每个 key 有独立的缓冲区和窗口起始时间
- **默认窗口**：50ms。窗口大小可通过 adapter 构造参数配置
- **合并行为**：窗口内的多个 delta 的 `text` 字段拼接为一条 `part.delta` 事件
- **不同 key 独立**：`text.append` 和 `thinking.append` 各自独立合并；不同 `part_index` 各自独立合并

### Flush 触发条件

Adapter **必须**在以下情况 flush 所有 pending 的合并 delta：

1. **窗口超时**：自窗口内第一个 delta 起 50ms 过去
2. **`part.end`**：同一 part 的 `part.end` 即将发出前，flush 该 key 的 pending delta
3. **非 delta 事件**：`tool.call` / `tool.result` / `part.start`（新 part）等非 delta 事件即将发出前，flush 所有 pending delta
4. **turn 结束**：`message.end` 即将发出前，flush 所有 pending delta

Flush 后，合并的 `part.delta` **必须在触发事件之前**发出，保证事件顺序：一个 part 的所有 delta 在其 `part.end` 之前。

### 不合并的事件

- `part.start` / `part.end` / `tool.call` / `tool.result` / `message.start` / `message.end` 等非 delta 事件**不合并**
- 不同 `delta.type` 的 delta **不互相合并**
- 不同 `part_index` 的 delta **不互相合并**

### 兼容性

旧客户端（不实现 rAF 批处理）收到合并后的 delta 仍能正确 append——`text` 字段语义不变，只是值更大。新客户端收到未合并的 delta 也能逐条 apply。无 breaking change。

---

## 事件流场景示例

### 场景 A：单聊，Agent 回复一段文字 + 一个产物

```
run.start         (runId=r1, agentId=cc)
message.start     (messageId=m1, agentId=cc, runId=r1)
part.start        (m1, 0, { type:'text', content:'' })
part.delta        (m1, 0, { type:'text.append', text:'好的，' })
part.delta        (m1, 0, { type:'text.append', text:'我为你写一个组件...' })
part.end          (m1, 0)
tool.call         (m1, c1, 'write_artifact', { ... })
artifact.create   ({ id:'a1', type:'web_app', ... })
tool.result       (m1, c1, { artifactId:'a1' }, false)
part.start        (m1, 1, { type:'artifact_ref', artifactId:'a1' })
tool.call         (m1, c2, 'deploy_artifact', { artifactId:'a1' })
tool.result       (m1, c2, { id:'dep1', status:'ready', previewPath:'/deployments/dep1' }, false)
deploy.status     (m1, { id:'dep1', status:'ready', ... })
part.start        (m1, 2, { type:'deploy_status', deployment:{...} })
part.end          (m1, 1)
message.end       (m1)
run.end           (r1, 'complete')
```

### 场景 B：群聊，Orchestrator 调度 2 个子 Agent 并行

```
run.start         (r1, orch)
message.start     (m1, orch, r1)
part.start        (m1, 0, { type:'thinking', content:'' })
part.delta        (...)
part.end          (m1, 0)
tool.call         (m1, c1, 'plan_tasks', ...)
dispatch.plan     (r1, [{ id:'t1', agentId:'pm', ... }, { id:'t2', agentId:'design', dependsOn:['t1'] }])
tool.result       (m1, c1, ...)
message.end       (m1)

dispatch.start    (r1, r2, t1, pm)
  run.start       (r2, pm, parentRunId=r1)
  message.start   (m2, pm, r2)
  ...
  run.end         (r2, 'complete')
dispatch.end      (parentRunId=r1, childRunId=r2, taskId=t1, status='complete')

dispatch.start    (r1, r3, t2, design)
  ...
dispatch.end      (parentRunId=r1, childRunId=r3, taskId=t2, status='complete')

# 聚合
message.start     (m4, orch, r1)
part.start        (m4, 0, { type:'text', content:'' })
part.delta        (...)
message.end       (m4)
run.end           (r1, 'complete')
```

若某任务因上游失败 / 中止被跳过，不会有 `dispatch.start` / child `run.start`，只发：

```
dispatch.end      (parentRunId=r1, taskId=t3, status='skipped', error='Upstream task did not complete')
```

---

## artifact_ref part 的注入路径

`artifact_ref` part **不由 Adapter 直接 emit**，由 AgentRunner 在接到 `artifact.create` 后注入到当前 message 的 parts 末尾。完整流程：

```
1. Adapter: yield tool.call(callId, 'write_artifact', args)
2. Adapter 内部 toolRegistry.execute → tool handler 写入 artifacts 表，返回 { artifactId, ... }
3. Adapter: yield tool.result(callId, { artifactId, ... }, isError=false)
4. Adapter: 检测 result.value.artifactId 非空 → 从 DB 拉完整 artifact 行
            yield artifact.create({ artifact: <row> })

5. AgentRunner 消费事件流，接到 artifact.create:
   - 找到当前 message（最近一条该 runId 下的 streaming message）
   - 给 message.parts 末尾 push 一个 { type: 'artifact_ref', artifactId }
   - 补发一个 part.start 事件让前端 reducer 同步

6. 前端 reducer 接 part.start → 在 messages[id].parts[nextIndex] 写入 artifact_ref part
```

**为什么不让 Adapter 直接 emit `part.start(artifact_ref)`**：保持「Adapter 只翻译事件，不操心 message.parts 结构」的边界。Adapter 知道「我创建了一个 artifact」，但不应关心「这条 message 的下一个 partIndex 是几」。AgentRunner 是唯一持有 message 流的角色，所以它来注入。

**前端无差别处理**：part.start 事件不区分是 Adapter emit 的还是 Runner 补的，reducer 都按 partIndex 写入。详见 Spec 09。

代码位置：`src/server/agent-runner.ts` 内 `consumeStream` 的 `artifact.create` 分支。

## deploy_status part 的注入路径

`deploy_status` part 同样由 AgentRunner 注入，不由 Adapter 直接发 `part.start`：

1. Adapter 执行 `deploy_artifact` / `deploy_workspace` 工具，得到 `DeployStatusRecord`
2. Adapter emit `tool.result`
3. Adapter emit `deploy.status`
4. AgentRunner 接到 `deploy.status` 后，在对应 message 末尾 push `{ type:'deploy_status', deployment }` 并补发 `part.start`

部署状态不单独建表；它是对一次消息输出的可视化状态记录。

---

## 持久化 vs 透传

| 事件 | 是否落库 | 备注 |
|---|---|---|
| `run.*` | ✅ 落到 `agent_runs` 表 | start/end 更新 status |
| `message.start` | ✅ 创建 message 记录（parts=[]） | Redis 可用时走 Stream write-behind 异步 INSERT；不可用时同步 INSERT |
| `message.end` | ✅ 更新 status='complete' | Redis 可用时走 Stream write-behind 异步 UPDATE；不可用时同步 UPDATE |
| `message.added` | ❌ 透传 | user 消息已由 `sendMessage` 落库；此事件仅广播给其它客户端，前端按 id 幂等 upsert（详见下方「用户消息广播」） |
| `message.removed` | ❌ 透传 | 撤回/编辑/重新生成已在服务端删库；此事件仅广播 messageIds/artifactIds 让其它客户端幂等移除 |
| `part.start` | ✅ 写入 `messages.parts[i]` | parts 整体作为 JSON 更新 |
| `part.delta` | ✅ 追加到 `messages.parts[i].content` | 高频写，可批量合并（每 100ms flush） |
| `part.end` | ❌ 透传 | parts 状态由 part.start/delta 已足够 |
| `tool.call` | ✅ 写为 message.parts 里的 tool_use part | |
| `tool.result` | ✅ 写为 message.parts 里的 tool_result part | run 失败 / 中止时，AgentRunner 会为未配对 `tool_result` 的 `tool_use` 补发 `isError=true` 结果 |
| `artifact.*` | ✅ 落到 `artifacts` 表 | |
| `dispatch.*` | ❌ 透传（信息来自 plan_tasks 工具调用 + agent_runs 表） | |
| `fs_write.pending` | ❌ 透传 | pending 队列存于内存单例（`src/server/pending-writes.ts`）；dev server 重启丢失，前端 mount 时拉一次兜底 |
| `fs_write.resolved` | ❌ 透传 | applied=true/false 由前端 store 用来移除对应 pending |
| `bash_command.pending` | ❌ 透传 | pending 队列存于内存单例（`src/server/pending-bash-commands.ts`）；前端 mount 时拉一次兜底 |
| `bash_command.resolved` | ❌ 透传 | approved=true/false 由前端 store 用来移除对应 pending |
| `run.usage` | ✅ 落到 `agent_runs.usage` JSON 列 | fire-and-forget `asyncio.create_task` 异步写入，不阻塞 SSE 推送 |
| `heartbeat` | ❌ 透传 | |
| `plan.created` | ❌ 透传 | 触发 consume_stream 注入 execution_plan part |
| `plan.step_update` | ✅ 更新 parts_buffer 中 execution_plan part 的 steps | 全量替换 steps 数组 |
| `file_write_preview.complete` | ✅ 更新 parts_buffer 中 file_write_preview part 的 status/path/oldContent/newContent | 对称于 plan.step_update 更新 execution_plan |

**写入策略**：所有可延迟事件（`message.start`/`message.end`/`part.*`/`tool.*`）在 Redis 可用时走 Stream write-behind：事件 XADD 到 per-run Redis Stream，后台 `DBWriterConsumer` 批量 flush 到 PG（`message.start` 走 `INSERT ... ON CONFLICT DO NOTHING`，其他走 `UPDATE parts`）。`run.usage`/`message.usage` 走 fire-and-forget `asyncio.create_task`。Redis 不可用时全部回退到同步写入。

**推送与持久化顺序**：`consume_stream` 中 `publish()` 在 `persist_event()` 之前执行，确保 SSE 推送不被远程 DB 写入阻塞。对需要从 `parts_buffer` 读取数据生成 `PartStartEvent` 的特殊事件（`artifact.create`/`deploy.status`/`plan.created` 等），parts_buffer 更新（纯内存操作）在 publish 之前完成，IO 部分（XADD / DB write）在 publish 之后执行。

```python
# 服务端伪代码（Python 移植后）
parts_buffer: dict[str, list[dict]] = {}  # messageId → in-memory parts

# consume_stream 主循环
async for event in stream:
    publish(event, user_id=user_id)   # SSE 推送先执行
    await persist_event(event, ...)   # DB 持久化后执行（XADD / fire-and-forget）

# DBWriterConsumer 后台批量 flush
# message.start → INSERT ... ON CONFLICT DO NOTHING
# 其他事件  → UPDATE messages SET parts = ...
```

---

## SSE 编码

```
data: {"type":"part.delta","conversationId":"conv_abc","timestamp":1715000000000,...}\n\n
```

**不使用 SSE 的 `event:` 字段**（每个事件类型不同 event 名，前端切分支麻烦）。统一 `data:` 一个字段，前端 `JSON.parse` 后 switch type。

**心跳间隔 15s**，防止中间代理 / 浏览器空闲断连。

---

## 不要做的事

- ❌ 不在事件中携带完整产物内容（用 `artifact.create` 单独事件 + 后续用 id 引用）
- ❌ 不为单个事件类型设计「事件 + 反向事件」（除生命周期 start/end 外）。状态机越简单越好
- ❌ 不在 Adapter 里发 `dispatch.*` 事件——这是 Orchestrator 调度器（AgentRunner 内部）的职责
- ❌ 不在事件 payload 里塞跟该事件无关的「全局状态」字段
- ❌ 不在 `part.delta` 里传完整 `text`（增量违反原则会导致后期重传放大）

---

## 兼容性

新增事件类型时：
1. 在 `StreamEvent` 联合类型中追加
2. 前端 reducer 增加 case；未实现的 case **必须** `default: ignore`（不报错）
3. 后端 EventBus 不需要改动（pass-through）

废弃事件类型时：
1. 先打 `@deprecated` 注释，保留至少 1 个版本
2. 验证所有 producer 已不再发该事件
3. 删除时同步删 reducer case
## Orchestrator plan review events

Plan review adds two dispatch events before the existing `dispatch.plan` execution event:

```typescript
| { type: 'dispatch.plan.pending', pendingPlan: PendingDispatchPlan }
| { type: 'dispatch.plan.resolved', pendingId: string, runId: string, approved: boolean }
```

`dispatch.plan.pending` means AgentRunner has compiled and validated a plan, registered it in the in-memory pending plan queue, and is waiting for user action. `dispatch.plan.resolved` removes that pending review from the UI. The existing `dispatch.plan` event remains the signal that an approved compiled plan is now executing.

These events are pass-through and are not persisted. The pending queue is recoverable through `GET /api/conversations/:id/pending-dispatch-plans` while the server process is alive.

## Execution Plan Events

`plan.created` and `plan.step_update` events enable real-time rendering of the Agent's structured execution plan in the UI.

**Flow:**
```
1. Agent calls create_plan tool → handler registers plan in plan_registry → returns { planId, steps, complexity }
2. _execute_tool_call_to_result detects create_plan success → appends PlanCreatedEvent
3. consume_stream receives plan.created → pushes execution_plan part + emits part.start
4. Agent calls plan_step → handler updates plan_registry → returns { planId, updatedSteps }
5. _execute_tool_call_to_result detects plan_step success → appends PlanStepUpdateEvent
6. consume_stream receives plan.step_update → updates execution_plan part steps in parts_buffer + SSE publish
7. On run.end: consume_stream finalizes all execution_plan parts (in_progress→done/failed, pending→skipped)
```

`plan.created` is pass-through (triggers part injection like `artifact.create`). `plan.step_update` updates the parts_buffer and is SSE-published for the frontend reducer to apply. Both are not independently persisted—the `execution_plan` part in the message parts carries the canonical state.

### Plan Step Auto-Update from Dispatch Events

When a dispatch task has `planStepId` set (coordinated mode), the system automatically updates the linked plan step status:

```
1. Orchestrator calls create_plan → plan_registry registers plan
2. Orchestrator calls dispatch_plan with planStepId on task items → handler registers mapping in plan_dispatch_mapping
3. dispatch.start event → consume_stream checks plan_dispatch_mapping → marks plan step as 'in_progress' → emits plan.step_update
4. dispatch.end event → consume_stream updates task status in plan_dispatch_mapping → aggregates all tasks for same (planId, stepId) → updates plan step status → emits plan.step_update
   - All tasks complete → step 'done'
   - Any task failed/aborted → step 'failed'
   - All tasks skipped → step 'skipped'
   - Some tasks still running → step remains 'in_progress'
5. On run.end: plan_dispatch_mapping and plan_registry are cleaned up
```

This auto-update only applies to dispatch tasks with `planStepId`. Steps the orchestrator executes itself must be manually updated with `plan_step`.

## File Write Preview Events

`file_write_preview.complete` events enable real-time rendering of file write previews in the UI.

**Flow:**
```
1. CustomAdapter detects tcd.function.name == "fs_write" → yields part.start(file_write_preview)
2. args_buffer increments → CustomAdapter feeds to _ContentExtractor → yields part.delta(file_write_preview.append)
3. _execute_tool_call_to_result executes fs_write → appends FileWritePreviewCompleteEvent
4. consume_stream receives file_write_preview.complete → updates file_write_preview part in parts_buffer + SSE publish
```

**Visibility**: `file_write_preview.complete` is in `_VISIBLE_EVENT_TYPES` — persisted to DB and published to SSE for non-hidden runs. Hidden runs (clone-subagent) persist but don't publish.

**Event fields**:
- `messageId` / `callId`: identifies the target file_write_preview part to update
- `path`: workspace-relative file path
- `oldContent`: previous file content (null for new files)
- `newContent`: final written content (null on failure)
- `status`: 'complete' on success, 'failed' on error
