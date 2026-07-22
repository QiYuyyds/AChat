## 1. DBWriterConsumer 新增 INSERT 路径

- [x] 1.1 修改 `DBWriterConsumer._flush_batch`，解析事件 JSON 中的 `type` 字段，区分 `message.start`（INSERT）和其他类型（UPDATE）
- [x] 1.2 对 `message.start` 事件，从 event JSON 提取 message 字段（id, conversation_id, agent_id, run_id, created_at, hidden），执行 `INSERT INTO messages (...) VALUES (...) ON CONFLICT (id) DO NOTHING`
- [x] 1.3 对其他类型事件，维持当前 `UPDATE messages SET parts = ... WHERE id = ...` 逻辑
- [x] 1.4 一个 batch 中混合 INSERT 和 UPDATE 时，按 message_id 分组，先执行所有 INSERT 再执行所有 UPDATE
- [x] 1.5 添加日志：记录每次 batch flush 的 INSERT 数量和 UPDATE 数量

## 2. persist_event 改造：message.start/end 走 Redis Stream

- [x] 2.1 修改 `persist_event` 中 `message.start` 分支：将 `async with get_db() as db: db.add(msg)` 改为 `await _persist_or_stream(redis_client, run_id, event, parts, use_stream)`，与 part/tool 事件一致
- [x] 2.2 修改 `persist_event` 中 `message.end` 分支：将同步 `UPDATE messages SET status='complete', parts=final_parts` 改为 `await _persist_or_stream(redis_client, run_id, event, parts, use_stream)`
- [x] 2.3 `message.end` 分支中删除 Redis Stream 的逻辑移到 `consume_stream` 的 `finally` 块（已有），避免 Consumer 还没 flush 就删了 Stream
- [x] 2.4 确保降级路径正确：Redis 不可用时，`_persist_or_stream` 回退到 `_update_message_parts`（已有逻辑），但 `message.start` 的降级需要走 INSERT 而非 UPDATE

## 3. persist_event 改造：usage 事件 fire-and-forget

- [x] 3.1 修改 `persist_event` 中 `run.usage` 分支：将同步 `async with get_db() as db: await db.execute(update(...))` 改为 `asyncio.create_task(_update_run_usage(event.run_id, event.usage))`
- [x] 3.2 修改 `persist_event` 中 `message.usage` 分支：改为 `asyncio.create_task(_update_message_usage(event.message_id, event.usage))`
- [x] 3.3 新增 `_update_run_usage` 和 `_update_message_usage` 辅助函数，封装 DB UPDATE 操作，异常时 log warning 不抛出
- [x] 3.4 确保降级路径：Redis 不可用时 usage 仍走 fire-and-forget（与 Redis 无关，usage 不走 Stream）

## 4. consume_stream 改造：publish 提前

- [x] 4.1 在 `consume_stream` 的 `async for event in stream` 循环中，将 `publish(event, ...)` 调用移到 `persist_event(...)` 之前
- [x] 4.2 对 `artifact.create` / `deploy.status` / `plan.created` / `plan.step_update` / `file_write_preview.complete` 等特殊事件（需要从 parts_buffer 读取数据生成 PartStartEvent），保持 parts_buffer 更新逻辑在 publish 之前，只将 IO 部分（XADD / DB write）移到 publish 之后
- [x] 4.3 确认 hidden=True 的 clone-subagent 运行不受影响（这些事件不 publish 但仍需 persist）

## 5. recovery_scan 兼容异步 INSERT

- [x] 5.1 修改 `recovery_scan._replay_stream_and_complete`：在回放 Stream 事件前，检查 message 行是否存在于 PG
- [x] 5.2 如果 message 行不存在，从 `message.start` 事件 JSON 中提取 message 字段，执行 INSERT
- [x] 5.3 如果 message 行存在，维持当前逻辑（回放 parts，标记 complete/interrupted）
- [x] 5.4 更新 `recovery_scan` 的日志，区分 "replayed + inserted" 和 "replayed + updated" 两种情况

## 6. 测试更新

- [x] 6.1 更新 `test_event_persistence_routing.py`：覆盖 `message.start` 走 Redis Stream 路径（Redis 可用时 XADD，不可用时同步 INSERT）
- [x] 6.2 更新 `test_event_persistence_routing.py`：覆盖 `message.end` 走 Redis Stream 路径
- [x] 6.3 新增测试：`run.usage` 和 `message.usage` 走 fire-and-forget 路径，验证 `create_task` 被调用且不阻塞
- [x] 6.4 更新 `test_recovery_scan.py`：覆盖 message 行不存在于 PG 但 Redis Stream 有 `message.start` 事件的场景
- [x] 6.5 新增测试：验证 publish 在 persist 之前执行（可以通过 mock 验证调用顺序）
- [x] 6.6 运行 `ruff check .` 和 `pytest` 确保所有测试通过

## 7. 文档同步

- [x] 7.1 更新 `specs/08-db-schema.md` 中关于 message 持久化时序的描述（如有）
- [x] 7.2 更新 `specs/02-stream-events.md` 中关于事件推送与持久化顺序的描述
- [x] 7.3 确认 `CLAUDE.md` 中 §3.3 "统一流式事件" 段落无需修改（事件契约不变，只变持久化时序）
