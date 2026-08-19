## 1. RetrievalConfig 扩展

- [x] 1.1 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `recall_top_k: int | None = None` 字段
- [x] 1.2 `backend/app/infra/hybrid.py`: 新增 `_resolve_recall_top_k(rc, top_k) -> int` 辅助函数：`rc.recall_top_k` → `rc.final_top_k * 4` → `top_k * 4`，最小 10

## 2. HybridResult source_info

- [x] 2.1 `backend/app/infra/hybrid.py`: `HybridResult` 新增 `source_info: dict = field(default_factory=dict)` 字段
- [x] 2.2 `backend/app/infra/hybrid.py`: `_load_chunks_by_ids` 改为 LEFT JOIN `documents` 表，返回 dict 包含 `title`、`source_path`、`parent_id`、`document_id`、`version_id`、`chunk_idx`、`start_char_pos`、`end_char_pos`
- [x] 2.3 `backend/app/infra/hybrid.py`: `_materialize_milvus_hits()`、`_search_hybrid()`、`_search_semantic()`、`_search_keyword()`、`_search_tfidf()` 等方法在构建 `HybridResult` 时填充 `source_info`
- [x] 2.4 `backend/app/infra/hybrid.py`: source_info 格式：`{"document_id": ..., "version_id": ..., "source_path": ..., "title": ..., "chunk_idx": ..., "start_char_pos": ..., "end_char_pos": ...}`

## 3. Milvus hybrid_search 使用 recall_top_k

- [x] 3.1 `backend/app/infra/hybrid.py`: `_search_hybrid()` 中 `fetch_k` 改为 `_resolve_recall_top_k(retrieval_config, top_k)`
- [x] 3.2 `backend/app/infra/hybrid.py`: `_search_milvus_hybrid()` 的 `top_k` 参数使用 recall_top_k 值
- [x] 3.3 `backend/app/infra/hybrid.py`: `_search_milvus_bm25()` 的 `top_k` 参数使用 recall_top_k 值
- [x] 3.4 `backend/app/infra/hybrid.py`: `_fetch_milvus()` 的 `top_k` 参数使用 recall_top_k 值
- [x] 3.5 `backend/app/infra/hybrid.py`: 最终截断仍用 `top_k`（即 `final_top_k`），在 `_search_hybrid` 末尾 `results[:top_k]`

## 4. RAGService / RAGEngine 透传

- [x] 4.1 `backend/app/rag/rag_engine.py`: `query()` 方法透传 `recall_top_k` 到 `HybridStore.search()`（通过 `RetrievalConfig`）
- [x] 4.2 `backend/app/services/rag_service.py`: `search()` 方法返回的 `HybridResult` 包含 `source_info`
- [x] 4.3 `backend/app/tools/memory_rag.py`: `rag_search` 工具 handler 输出中包含 chunk 的 `source_info`（`documentId`、`sourcePath`、`title`、`chunkIdx`）

## 5. 降级路径兼容

- [x] 5.1 `backend/app/infra/hybrid.py`: TF cosine fallback (`_search_tfidf`) 和 PG embedding fallback (`_search_pg_embedding`) 也使用 recall_top_k 作为召回数量
- [x] 5.2 `backend/app/infra/hybrid.py`: fallback 路径也填充 `source_info`（从 `_load_chunks_by_ids` 获取）

## 6. 验证

- [x] 6.1 `ruff check .` 通过
- [x] 6.2 `pytest` 通过
- [ ] 6.3 手动测试：`RetrievalConfig(recall_top_k=50, final_top_k=10)` 确认 Milvus 召回 50 条，最终返回 10 条
- [ ] 6.4 手动测试：`HybridResult.source_info` 包含 `document_id`、`source_path`、`title` 等字段
- [ ] 6.5 手动测试：不传 `recall_top_k` 时默认使用 `top_k * 4`
- [ ] 6.6 手动测试：Milvus 不可用时 fallback 路径也正确填充 `source_info`
