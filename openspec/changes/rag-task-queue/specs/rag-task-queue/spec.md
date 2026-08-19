## rag-task-queue

### RAG task queue — independent from global Task Board

- **Table**: `rag_tasks` (local SQLite, separate from `tasks`).
- **Worker**: `RagTaskWorker` — single asyncio background task, polls `pending` tasks every `rag_task_worker_interval` (default 5s), processes serially.
- **Task types**: `parse`, `ingest`, `graph_build`, `delete_cleanup`.
- **State machine**: `pending` → `running` → `completed` / `failed` (retryable up to `max_retries`) → `failed_permanent` (terminal, requires manual retry).
- **Stale recovery**: on worker startup, `running` tasks are marked `failed` with message `'Stale task recovered on restart'`.
- **Pipeline**: `upload_file` → creates `RagTask(type='ingest')` → worker picks up → `_ingest_content` → creates `RagTask(type='graph_build')` → worker picks up → `GraphBuildTask.build`.
- **API**: `GET /api/rag-tasks`, `GET /api/rag-tasks/{id}`, `POST /api/rag-tasks/{id}/retry`.
- **Degradation**: when `rag_task_worker_enabled=False`, `upload_file` executes synchronously (same as pre-proposal behavior).
- **`graph_build_config` storage**: in `RagTask.payload` JSON field (per-task, not per-document).
