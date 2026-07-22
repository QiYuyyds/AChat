# Design: Optimize Stream Persistence

## Context

AChat 的 Agent 事件流通过 `consume_stream` 处理，每个 StreamEvent 的处理顺序是 `persist_event → publish`。当 PostgreSQL 部署在远程服务器（如 64.83.35.253，RTT 50~100ms）时，同步 DB 写入阻塞 SSE 推送，导致流式响应卡顿。

当前状态：
- **高频事件**（part.start/delta/end、tool.call/result）已通过 Redis Stream write-behind 异步写入 PG，不阻塞 publish
- **低频事件**（message.start INSERT、message.end UPDATE、run.usage UPDATE、message.usage UPDATE）仍走同步 PG，阻塞 publish
- `recovery_scan.py` 已实现崩溃恢复：启动时扫描 `status=streaming` 的 message，从 Redis Stream 回放事件重建 parts
- `DBWriterConsumer` 已实现 Consumer Group 模式，通过 `XREADGROUP` 批量消费并 flush 到 PG

## Goals / Non-Goals

### Goals

- 消除 `consume_stream` 热路径中的所有同步远程 PG 写入
- `publish()` 对所有事件类型立即执行，不受 DB 写入延迟影响
- 复用已有的 Redis Stream + DBWriterConsumer + recovery_scan 基础设施
- Redis 不可用时自动降级到当前同步写入路径

### Non-Goals

- 不改变 StreamEvent 的事件类型契约（事件 schema 不变）
- 不改变前端 store reducer 逻辑（前端不感知持久化时序变化）
- 不改变 message 的 parts 结构和渲染逻辑
- 不涉及 PostgreSQL 部署位置变更（那是运维决策）
- 不引入新的基础设施依赖（Redis 已有）

## Decisions

### D1: message.start 改为 XADD 到 Redis Stream，Consumer 异步 INSERT

**选择**: 将 `message.start` 的 INSERT 也变成 deferrable 事件，XADD 到 Redis Stream，由 `DBWriterConsumer` 异步执行 `INSERT ... ON CONFLICT DO NOTHING`。

**理由**:
- `message.start` 是每条 message 的第一次同步 PG 写入，阻塞最严重（在第一个 token 到达前端之前）
- Consumer 已有 `parts_buffer` 注册机制，可以在 flush 时先确保 message 行存在再 UPDATE parts
- `ON CONFLICT DO NOTHING` 保证幂等——Consumer 和 `message.end` 的同步 flush（降级路径）不会冲突
- `recovery_scan.py` 已能从 Redis Stream 回放重建 message 状态

**替代方案**:
- 先 publish 再同步 INSERT：减少阻塞但 INSERT 失败时前端已显示 message，后续 UPDATE 找不到行。需要额外补偿逻辑
- message.start 不走 Stream 而用 `asyncio.create_task` 异步 INSERT：比 XADD 轻量，但失去 Consumer Group 的批量化和背压能力

### D2: message.end 改为 XADD 到 Redis Stream，Consumer 异步 UPDATE

**选择**: `message.end` 的 UPDATE（final parts + status='complete'）也走 Redis Stream，Consumer 异步执行。

**理由**:
- `message.end` 在 message 末尾执行，阻塞下一条 message 的开始
- Consumer flush 时 message 行已存在（由 message.start 的异步 INSERT 保证），UPDATE 可以安全执行
- 如果 Consumer 还没来得及 flush，message 仍在 `status=streaming`，`recovery_scan` 兜底

**替代方案**:
- 保留 message.end 同步：每条 message 末尾仍有一次远程 RTT 阻塞。改善有限

### D3: run.usage 和 message.usage 改为 fire-and-forget

**选择**: usage 事件用 `asyncio.create_task` fire-and-forget，不走 Redis Stream。

**理由**:
- usage 是低频、幂等、非关键路径的 UPDATE（last-write-wins 语义）
- 不需要 Consumer 的批量化和背压
- 如果 `create_task` 的 DB 写入失败，仅丢失 usage 统计，不影响对话内容

**替代方案**:
- 也走 Redis Stream：过于重量，usage 事件不涉及 parts_buffer 的读写，无需 Consumer 介入

### D4: consume_stream 中 publish 提前到 persist 之前

**选择**: 将 `consume_stream` 中的 `publish(event)` 调用移到 `persist_event(event)` 之前。

```
当前:  await persist_event(...)  →  publish(...)
改为:  publish(...)  →  await persist_event(...)
```

**理由**:
- persist_event 对 deferrable 事件只是 XADD 到本地 Redis（sub-ms），对 usage 是 fire-and-forget，都不阻塞
- publish 是 `put_nowait` 到 asyncio.Queue，本身不阻塞
- 调换顺序后，前端立即收到事件，DB 写入在后台异步进行

**注意**: parts_buffer 的更新仍在 `persist_event` 内完成。但 publish 对大多数事件只是转发 event 本身，不依赖 parts_buffer。唯一例外是 `artifact.create` / `deploy.status` / `plan.created` 等事件在 publish 时会额外发送一个 `PartStartEvent`，这些 part 数据来自 parts_buffer——需要确保 persist_event 中 parts_buffer 的更新在 publish 之前完成。

**实际处理**: 将 `persist_event` 中的 parts_buffer 更新逻辑提前执行（它是纯内存操作，不涉及 IO），IO 部分（XADD / DB write）在 publish 之后执行。或者更简单：对这些特殊事件，保持 persist→publish 顺序（它们是低频事件），只对高频的 part/tool 事件和 message.start/end 调换顺序。

### D5: DBWriterConsumer 新增 INSERT 路径

**选择**: `DBWriterConsumer._flush_batch` 在处理 `message.start` 类型的 Stream 事件时，先执行 `INSERT ... ON CONFLICT DO NOTHING`，再执行 UPDATE parts。

**实现**:
- Consumer 解析 event JSON 中的 `type` 字段
- `message.start` → `INSERT INTO messages (id, ...) VALUES (...) ON CONFLICT DO NOTHING`
- 其他类型 → 维持当前 `UPDATE messages SET parts = ...`
- 一次 batch 可能混合 INSERT 和 UPDATE，按 message_id 分组合并

## Risks / Trade-offs

### [R1: 用户刷新页面时 message 行尚未 INSERT]

Consumer 异步 INSERT 可能有 ~1s 延迟（BLOCK_MS=1000）。如果用户在这期间刷新页面，REST API `GET /api/conversations/{id}/messages` 查不到这条 message。

**缓解**: 前端在 SSE 事件到达时已在 store 中缓存了 message 数据。刷新时先用 store 中的缓存渲染，REST API 返回的数据作为 reconcile。如果 REST API 返回的缺少最近一条 message，前端不删除已有缓存——这是 optimistic UI 的标准模式。

### [R2: 进程崩溃时 message 行未 INSERT 但 Redis Stream 有事件]

进程崩溃时 Consumer 可能还没 flush。重启后 `recovery_scan` 扫描 `status=streaming` 的 message——但 message 行可能还不存在（INSERT 还没执行）。

**缓解**: `recovery_scan` 需要 extended：扫描 Redis Stream 中的 `message.start` 事件，如果对应的 message 行不存在，先 INSERT 再回放。当前的 `recovery_scan` 已经从 Stream XRANGE 回放事件重建 parts，只需在回放前检查 message 行是否存在并补 INSERT。

### [R3: BLOCK_MS=1000 导致 flush 延迟]

Consumer 的 `XREADGROUP BLOCK 1000` 意味着事件最多延迟 1s 到达 PG。对于 message.start，这意味着 message 行最多 1s 后才出现在 PG。

**缓解**: 1s 延迟对用户体验无感知（前端已通过 SSE 看到 message）。如果需要更低延迟，可以调小 BLOCK_MS 或改用 `XADD` 时 notify Consumer（但增加复杂度，暂不需要）。

### [R4: Redis 不可用时降级到同步写入]

Redis 挂了时 `use_stream=False`，所有事件回退到同步 PG 写入。这恢复到当前行为——慢但正确。

**缓解**: 降级路径已有且测试覆盖。无需额外处理。

## Migration Plan

1. 修改 `DBWriterConsumer._flush_batch` 支持 INSERT 路径
2. 修改 `persist_event` 将 message.start/end 改为 `_persist_or_stream`
3. 修改 `persist_event` 将 usage 事件改为 `asyncio.create_task`
4. 修改 `consume_stream` 调换 publish/persist 顺序（对 deferrable 事件）
5. Extended `recovery_scan` 在回放前检查 message 行是否存在
6. 更新测试覆盖新路径
7. 无 DB schema 变更，无前端变更，可安全部署

**回滚**: 恢复 `persist_event` 中 message.start/end 的同步写入路径即可。Redis Stream write-behind 已是增量开关（`use_stream = redis_client is not None`），关闭 Redis 即回退。
