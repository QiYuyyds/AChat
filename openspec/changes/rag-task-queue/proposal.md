## Why

AChat RAG 系统的长时间运行任务（文档解析、RAG 索引、图谱构建）当前以 `asyncio.create_task` fire-and-forget 方式执行。这导致：任务状态不可查询、失败不可重试、并发不可控、重启后任务丢失。全局 Task Board 是面向 Agent 派发的任务池，不适合管理 RAG 内部流水线任务。

本提案建立独立的 RAG 任务队列，与全局 Task Board 分离，专门管理 RAG 生命周期内的异步任务。

## What Changes

- **新增 `RagTask` 模型**：持久化的 RAG 任务记录，路由到**本地 SQLite**（与 Task Board 一致，热数据本地存储）
- **新增 `RagTaskWorker`**：asyncio 后台 worker，轮询 `pending` 状态的 RAG 任务，按 `task_type` 分发到对应的 handler
- **任务类型**：`parse`（文件解析）、`ingest`（RAG 索引）、`graph_build`（图谱构建）、`delete_cleanup`（删除清理）
- **`DocumentService.upload_file` 改为入队**：文件上传后创建 `RagTask(type='parse')`，worker 异步处理解析 → 索引 → 图谱构建流水线
- **状态流转**：`pending` → `running` → `completed` / `failed`；支持重试（`max_retries=3`）
- **API 端点**：`GET /api/rag-tasks`（列表）、`GET /api/rag-tasks/{id}`（详情）、`POST /api/rag-tasks/{id}/retry`（重试）
- **与全局 Task Board 完全分离**：独立的表、独立的 worker、独立的 API 前缀

## Capabilities

### New Capabilities

- `rag-task-queue`: RAG 专用任务队列——持久化任务记录 + asyncio 后台 worker + 重试机制

### Modified Capabilities

- `persistence`: 新增 `rag_tasks` 表（本地 SQLite 路由）

## Impact

- **新增文件**: `backend/app/db/models.py`（RagTask 模型）、`backend/app/services/rag_task_worker.py`（worker 服务）、`backend/app/api/rag_tasks.py`（API 路由）、`backend/app/schemas/rag_task.py`（Pydantic 模型）
- **修改文件**: `backend/app/services/document_service.py`（upload_file 改为入队）、`backend/app/main.py`（lifespan 启动 worker）、`backend/app/db/table_routing.py`（新增 rag_tasks 到 LOCAL_TABLES）、`backend/app/config.py`（新增 worker 配置项）
- **DB 迁移**: 新建 `rag_tasks` 表（SQLite 自动 `create_all`）
- **与全局 Task Board 区分**: 独立表 `rag_tasks`（不共享 `tasks` 表），独立 worker `RagTaskWorker`（不复用 `TaskSchedulerService`），独立 API `/api/rag-tasks`（不复用 `/api/tasks`）
