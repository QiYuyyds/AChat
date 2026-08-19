## Context

AChat 已有两套异步执行机制：

1. **`asyncio.create_task` fire-and-forget**：当前 `DocumentService.upload_file` → `GraphBuildTask.build()` 用这种方式。问题：不可查询、不可重试、重启丢失。
2. **`TaskSchedulerService` + `tasks` 表（全局 Task Board）**：面向 Agent 派发的任务池，用户/Agent 创建 todo 任务，scheduler 派发给指派 Agent 执行。用途完全不同——是 Agent 的工作任务，不是 RAG 内部流水线任务。

用户明确要求：**RAG 任务队列与全局 Task Board 区分开来**。

Fidi-Intelli 的做法：有独立的 RAG 任务队列，文档上传后入队，worker 异步处理 parse → chunk → embed → index → graph_build 流水线。

## Goals / Non-Goals

**Goals:**
- 新建 `rag_tasks` 表（本地 SQLite），持久化 RAG 任务
- 新建 `RagTaskWorker`，asyncio 后台轮询 + 分发
- `DocumentService.upload_file` 改为创建 RagTask 入队，不再 fire-and-forget
- 任务状态可查询（API + SSE 事件可选）
- 失败重试（`max_retries` 配置项）
- 与全局 Task Board 完全分离

**Non-Goals:**
- 不复用 `tasks` 表或 `TaskSchedulerService`
- 不实现 DAG 依赖调度（RAG 任务是线性流水线，不需要 DAG）
- 不实现任务优先级（RAG 任务量小，FIFO 足够）
- 不实现跨进程任务调度（单进程 asyncio 足够）
- 不修改前端 UI

## Decisions

### Decision 1: 独立表 `rag_tasks` 而非复用 `tasks` 表

**Choice**: 新建 `rag_tasks` 表，路由到本地 SQLite（`LOCAL_TABLES`），与全局 Task Board 的 `tasks` 表完全分离。

**Rationale**: 全局 Task Board 的 `tasks` 表有 `assignee_agent_id`、`priority`、`labels`、`due_date` 等 Agent 任务专属字段，RAG 任务不需要。RAG 任务有 `task_type`、`document_id`、`version_id` 等 RAG 专属字段。混用会导致表结构臃肿、查询效率下降。用户明确要求分离。

**Alternative considered**: 复用 `tasks` 表 + `task_type='rag_*'` 过滤。否决——表语义混乱，字段不匹配。

### Decision 2: 单 worker asyncio 轮询而非多 worker 并发

**Choice**: 单个 `RagTaskWorker` 实例，asyncio 后台 task，按 `interval_seconds`（默认 5s）轮询 `pending` 任务，串行处理（一次只处理一个任务）。

**Rationale**: RAG 任务量小（每天几十个），串行处理足够。并发处理需要管理资源竞争（Milvus 写入、embedding API 限流），增加复杂度。如果后续需要并发，加 `max_concurrent` 配置项即可。

**Alternative considered**: 多 worker 并发。否决——当前量级不需要，且并发控制复杂。

### Decision 3: 任务状态流转

**Choice**: `pending` → `running` → `completed` / `failed`（`failed` 可重试回 `pending`）。

```
pending → running → completed (终态)
                  → failed (可重试, max_retries 次后变为 failed_permanent)
failed → pending (手动 retry 或自动重试)
```

**Rationale**: 简单的线性状态机。不需要 `in_review` / `blocked` 等 Task Board 的协作状态。`failed_permanent` 是终态，需要手动干预。

**Alternative considered**: 加入 `cancelled` 状态。暂不实现——RAG 任务通常很快完成，取消需求低。后续需要时加。

### Decision 4: `DocumentService.upload_file` 改为入队

**Choice**: `upload_file` 完成文件解析后，创建 `RagTask(type='ingest', document_id=..., version_id=...)`，返回任务 ID 给调用方。worker 拾取后执行 `_ingest_content` → `GraphBuildTask.build`。

**Rationale**: 当前 `upload_file` 在请求处理中同步执行 `_ingest_content`（包括 embedding 调用、Milvus 写入等），可能导致 HTTP 请求超时。改为入队后，`upload_file` 只负责文件解析 + 创建 Document + 入队，快速返回。worker 异步处理索引。

**Alternative considered**: 保持同步 + 用 SSE 推送进度。否决——同步执行可能导致超时，且无法重试。

### Decision 5: `rag_tasks` 表路由到本地 SQLite

**Choice**: `rag_tasks` 加入 `LOCAL_TABLES`，路由到本地 SQLite（与 `tasks` / `task_comments` 一致）。

**Rationale**: RAG 任务是热数据（频繁查询状态、频繁更新），本地 SQLite 延迟低。与全局 Task Board 一致的路由策略。不需要多用户共享 RAG 任务（每个用户有自己的 RAG 任务，通过 `user_id` 隔离）。

**Alternative considered**: 路由到 PostgreSQL。否决——RAG 任务频繁状态更新，PG 延迟高于 SQLite 本地。

### Decision 6: `graph_build_config` 存储在 `RagTask.payload` JSON 字段

**Choice**: `RagTask` 有 `payload: JSON` 字段，`graph_build` 任务的 `GraphBuildConfig` 序列化在 payload 中。

**Rationale**: `GraphBuildConfig` 是任务参数，不是 Document 级配置（同一文档不同版本可能用不同的图谱构建参数）。存在 task payload 中天然随任务走。

**Alternative considered**: 存在 `Document.graph_config` 列。否决——增加 Document 模型复杂度，且不同版本可能需要不同配置。

## Risks / Trade-offs

- **[Risk] worker 单点故障** → 单进程 asyncio，进程崩溃时 running 状态任务标记为 failed。重启后手动 retry。
- **[Risk] 任务积压** → RAG 任务量小（每天几十个），串行处理足够。如果积压可加 `max_concurrent`。
- **[Risk] 重启后 running 任务卡住** → worker 启动时扫描 `running` 状态任务，标记为 `failed`（stale recovery）。
- **[Risk] SQLite 并发写入** → `rag_tasks` 在 SQLite WAL 模式下，单写入 + 多读取，worker 是唯一写入者。

## Migration Plan

1. `models.py` 中声明 `RagTask` 模型
2. `table_routing.py` 中将 `rag_tasks` 加入 `LOCAL_TABLES`
3. `create_all` 在 SQLite 上自动建表
4. 无需迁移脚本（新表，无旧数据）

## Open Questions

无——所有决策点已在讨论中确认。
