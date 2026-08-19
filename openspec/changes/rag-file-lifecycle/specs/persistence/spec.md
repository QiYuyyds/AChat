## persistence

### `documents` table — new columns

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `parent_id` | `VARCHAR(64)` | YES | NULL | Self-FK `documents(id) ON DELETE SET NULL` |
| `is_folder` | `BOOLEAN` | NO | FALSE | Distinguishes folder vs file nodes |

### `rag_chunks` table — no changes

### Migration

- `ALTER TABLE documents ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64)`
- `ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_folder BOOLEAN DEFAULT FALSE`
- `UPDATE documents SET is_folder = FALSE WHERE is_folder IS NULL`
- New index: `idx_documents_parent_id` on `parent_id`
