## persistence

### New table: `rag_tasks`

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | `VARCHAR` (PK) | NO | — | Primary key |
| `user_id` | `VARCHAR` (FK users.id) | NO | — | Owner isolation |
| `task_type` | `VARCHAR(32)` | NO | — | `parse` / `ingest` / `graph_build` / `delete_cleanup` |
| `document_id` | `VARCHAR` (FK documents.id ON DELETE SET NULL) | YES | NULL | Associated document |
| `version_id` | `VARCHAR` (FK document_versions.id ON DELETE SET NULL) | YES | NULL | Associated version |
| `status` | `VARCHAR(16)` | NO | `pending` | `pending` / `running` / `completed` / `failed` / `failed_permanent` |
| `payload` | `JSONB` | NO | `{}` | Task parameters (e.g. GraphBuildConfig) |
| `result` | `JSONB` | YES | NULL | Task result (e.g. chunk_count) |
| `error_message` | `TEXT` | YES | NULL | Failure reason |
| `retry_count` | `INTEGER` | NO | 0 | Current retry count |
| `max_retries` | `INTEGER` | NO | 3 | Max retry attempts |
| `created_at` | `FLOAT` | NO | — | Epoch seconds |
| `updated_at` | `FLOAT` | NO | — | Epoch seconds |
| `started_at` | `FLOAT` | YES | NULL | When task started running |
| `completed_at` | `FLOAT` | YES | NULL | When task completed/failed |

### Indexes

- `idx_rag_tasks_status` on `status`
- `idx_rag_tasks_user` on `user_id`
- `idx_rag_tasks_doc` on `document_id`

### Table routing

- `rag_tasks` is routed to **local SQLite** (`LOCAL_TABLES`), same as `tasks` and `task_comments`.
- Completely separate from the global Task Board `tasks` table.
