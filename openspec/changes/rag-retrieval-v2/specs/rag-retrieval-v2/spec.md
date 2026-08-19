## persistence

### No schema changes

All required columns (`document_id`, `version_id`, `start_char_pos`, `end_char_pos` on `rag_chunks`) were added by `rag-overhaul-foundation`. This proposal leverages those columns for source backfill via LEFT JOIN.

## rag-retrieval-v2

### Recall top_k

- `RetrievalConfig.recall_top_k: int | None = None` — independent recall-stage top_k.
- Default: `final_top_k * 4` (or `top_k * 4` when `final_top_k` is also None), minimum 10.
- Used by: `_search_milvus_hybrid`, `_search_milvus_bm25`, `_fetch_milvus`, TF cosine fallback, PG embedding fallback.
- Final truncation still uses `final_top_k` / `top_k`.

### Chunk source backfill

- `HybridResult.source_info: dict` — document-level metadata for each chunk.
- Format: `{"document_id": str, "version_id": str, "source_path": str, "title": str, "chunk_idx": int, "start_char_pos": int|None, "end_char_pos": int|None}`.
- Populated by `_load_chunks_by_ids` which LEFT JOINs `documents` table.

### MilvusRetrievalConfig alignment

- Milvus `hybrid_search` `limit` parameter uses `recall_top_k` (not `final_top_k`).
- `WeightedRanker` weights (`vector_weight`, `bm25_weight`) transparently passed from `RetrievalConfig`.
- `bm25_drop_ratio_search` passed to BM25 sparse search.
