## 1. RagTask 模型

- [x] 1.1 `backend/app/db/models.py`: 新增 `RagTask` 模型，列定义：
  - `id: str` (PK)
  - `user_id: str` (FK users.id, nullable=False)
  - `task_type: str` (nullable=False) — `parse` | `ingest` | `graph_build` | `delete_cleanup`
  - `document_id: str | None` (FK documents.id, ON DELETE SET NULL, nullable=True)
  - `version_id: str | None` (FK document_versions.id, ON DELETE SET NULL, nullable=True)
  - `status: str` (nullable=False, default='pending') — `pending` | `running` | `completed` | `failed` | `failed_permanent`
  - `payload: dict` (JSONB, default={}) — 任务参数（如 GraphBuildConfig、parse options）
  - `result: dict | None` (JSONB, nullable=True) — 任务结果（chunk_count 等）
  - `error_message: str | None` (Text, nullable=True)
  - `retry_count: int` (nullable=False, default=0)
  - `max_retries: int` (nullable=False, default=3)
  - `created_at: float` (nullable=False)
  - `updated_at: float` (nullable=False)
  - `started_at: float | None` (nullable=True)
  - `completed_at: float | None` (nullable=True)
- [x] 1.2 `backend/app/db/models.py`: `__table_args__` 定义索引：`Index("idx_rag_tasks_status", "status")`、`Index("idx_rag_tasks_user", "user_id")`、`Index("idx_rag_tasks_doc", "document_id")`
- [x] 1.3 `backend/app/db/table_routing.py`: `LOCAL_TABLES` 新增 `"rag_tasks"`

## 2. Pydantic Schemas

- [x] 2.1 `backend/app/schemas/rag_task.py`: 新建文件
- [x] 2.2 定义 `RagTaskResponse` 模型（camelCase 别名：`taskId`、`taskType`、`documentId`、`versionId` 等）
- [x] 2.3 定义 `RagTaskListResponse` 模型（`tasks: list[RagTaskResponse]`）
- [x] 2.4 定义 `CreateRagTaskRequest` 模型（`taskType`、`documentId`、`versionId`、`payload`）
- [x] 2.5 `backend/app/schemas/__init__.py`: 导出新模型

## 3. RagTaskWorker

- [x] 3.1 `backend/app/services/rag_task_worker.py`: 新建 `RagTaskWorker` 类（singleton 模式，类似 `TaskSchedulerService`）
- [x] 3.2 实现 `async def start(self, interval_seconds: int = 5)`: 创建 asyncio 后台 task 轮询 `pending` 任务
- [x] 3.3 实现 `async def stop(self)`: 取消后台 task
- [x] 3.4 实现 `async def _scan_and_dispatch(self)`: 查询 `status='pending'` 的任务（按 `created_at ASC`），取第一个，标记为 `running`，按 `task_type` 分发
- [x] 3.5 实现 stale recovery：worker 启动时扫描 `status='running'` 的任务，标记为 `failed`（error_message='Stale task recovered on restart'）
- [x] 3.6 实现 `async def _execute_task(self, task: RagTask)`: 按 `task_type` 调用对应 handler
- [x] 3.7 实现 handler 路由：`_handle_parse`、`_handle_ingest`、`_handle_graph_build`、`_handle_delete_cleanup`
- [x] 3.8 `_handle_ingest`：调用 `DocumentService._ingest_content()` → 完成后如果 `rag_graph_auto_build=True` 创建 `RagTask(type='graph_build')` 入队
- [x] 3.9 `_handle_graph_build`：调用 `GraphBuildTask.build()`，payload 中读取 `GraphBuildConfig`
- [x] 3.10 实现重试逻辑：任务失败时 `retry_count += 1`，如果 `< max_retries` 则状态回 `pending`，否则 `failed_permanent`
- [x] 3.11 实现结果写入：任务成功后 `result` 字段写入返回值

## 4. DocumentService 改造

- [x] 4.1 `backend/app/services/document_service.py`: `upload_file()` 在 `write_document()` 完成后，创建 `RagTask(type='ingest', document_id=..., version_id=...)` 入队
- [x] 4.2 `upload_file()` 返回值新增 `rag_task_id: str` 字段
- [x] 4.3 `_ingest_content()` 保持不变（由 worker 调用）
- [x] 4.4 `ingest_version()` 改为创建 `RagTask(type='ingest')` 入队，不再同步执行

## 5. API 端点

- [x] 5.1 `backend/app/api/rag_tasks.py`: 新建 API router
- [x] 5.2 `GET /api/rag-tasks` — 列表（支持 `status` / `document_id` / `task_type` 过滤）
- [x] 5.3 `GET /api/rag-tasks/{id}` — 单个任务详情
- [x] 5.4 `POST /api/rag-tasks/{id}/retry` — 重试失败任务（`status` 回 `pending`，`retry_count` 重置为 0）
- [x] 5.5 `backend/app/main.py`: 注册 `rag_tasks.router`
- [x] 5.6 所有端点使用 `get_current_user` 认证 + `user_id` 隔离

## 6. 配置项

- [x] 6.1 `backend/app/config.py`: 新增配置项：
  - `rag_task_worker_interval: int = 5` — worker 轮询间隔（秒）
  - `rag_task_max_retries: int = 3` — 最大重试次数
  - `rag_task_worker_enabled: bool = True` — 是否启用 worker

## 7. 启动集成

- [x] 7.1 `backend/app/main.py`: lifespan 中在 `RAGService.initialize()` 后启动 `RagTaskWorker`
- [x] 7.2 lifespan shutdown 中停止 `RagTaskWorker`
- [x] 7.3 `rag_task_worker_enabled=False` 时不启动 worker（降级为同步执行）

## 8. 验证

- [x] 8.1 `ruff check .` 通过
- [x] 8.2 `pytest` 通过
- [x] 8.3 手动测试：上传文件后 `GET /api/rag-tasks` 返回 `ingest` 类型任务
- [x] 8.4 手动测试：任务状态从 `pending` → `running` → `completed`
- [x] 8.5 手动测试：任务失败后自动重试 `max_retries` 次，最终 `failed_permanent`
- [x] 8.6 手动测试：`POST /api/rag-tasks/{id}/retry` 将 `failed_permanent` 回 `pending`
- [x] 8.7 手动测试：重启后 `running` 状态任务被标记为 `failed`
- [x] 8.8 手动测试：`rag_task_worker_enabled=False` 时 `upload_file` 同步执行（降级模式）
