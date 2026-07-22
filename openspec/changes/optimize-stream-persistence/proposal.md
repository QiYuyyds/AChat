# Optimize Stream Persistence

## Why

当前 `consume_stream` 对 `message.start`（INSERT）、`message.end`（UPDATE）、`run.usage`（UPDATE）、`message.usage`（UPDATE）四类事件执行同步远程 PG 写入，而 `publish()` 在 `persist_event()` 之后才调用。当 PostgreSQL 部署在远程服务器时（RTT 50~100ms），每条 message 产生 3~4 次同步远程写入，累计 150~400ms 的纯阻塞，直接导致 SSE 流式推送卡顿。高频事件（part.delta 等）已通过 Redis Stream write-behind 解决，但低频同步写入仍是瓶颈。

## What Changes

- `message.start` 事件从同步 INSERT 改为 XADD 到 Redis Stream，由 `DBWriterConsumer` 异步批量 INSERT（`INSERT ... ON CONFLICT DO NOTHING`）
- `message.end` 事件从同步 UPDATE 改为 XADD 到 Redis Stream，由 `DBWriterConsumer` 异步批量 UPDATE
- `run.usage` 和 `message.usage` 事件改为 fire-and-forget 异步写入（`asyncio.create_task`），不阻塞 publish
- `consume_stream` 中 `publish()` 调用提前到 `persist_event()` 之前执行，确保前端立即收到事件
- `DBWriterConsumer._flush_batch` 新增 INSERT 路径（当前只支持 UPDATE parts）
- `recovery_scan.py` 已有的 Stream 回放机制覆盖 message.start 异步 INSERT 场景（无需修改，自然兼容）
- Redis 不可用时，`persist_event` 回退到当前同步写入路径（降级不变）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `stream-events`: 事件推送与持久化的顺序契约变更——publish 在 persist 之前执行，SSE 推送不再被远程 DB 写入阻塞
- `persistence`: 事件持久化策略变更——所有 deferrable 事件（含 message.start/end、usage）统一走 Redis Stream write-behind，PG 写入完全异步

## Impact

- **`backend/app/services/agent_runner.py`**: `persist_event` 函数重构，`consume_stream` 中 publish/persist 顺序调换
- **`backend/app/services/async_db_writer.py`**: `DBWriterConsumer._flush_batch` 新增 INSERT 路径，支持 `message.start` 的异步 INSERT
- **`backend/app/services/recovery_scan.py`**: 无需修改（已有回放逻辑兼容异步 INSERT 场景）
- **`backend/app/schemas/events.py`**: 无需修改（事件契约不变，只变持久化时序）
- **`src/shared/`**: 无需修改（前端不感知持久化时序变化）
- **`backend/tests/`**: 需更新 `test_event_persistence_routing.py` 等测试用例覆盖新的异步路径
