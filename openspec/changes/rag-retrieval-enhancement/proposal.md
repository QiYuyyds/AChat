## Why

AChat RAG 检索层缺少 BM25 全文检索能力且参数固定无法动态调整，无法支持评估系统按不同参数组合跑评测。并发控制也缺失，大量并发检索请求可能导致 Milvus 过载。

> **方案更新**：原提案保留 ES 作为 BM25 主路径。方案文档后续更新（决策点 2 + 12）确认**完全移除 ES**，BM25 改用 Milvus native BM25。ES 移除和 Milvus native BM25 实现由 `rag-milvus-bm25-migration` 提案执行。本提案保留 PG TF cosine fallback、RetrievalConfig 查询参数化、并发控制，但删除 ES analyzer 增强相关 task。

## What Changes

- 新增 PG TF cosine fallback 搜索路径：当 Milvus 不可用时，在 PG 的 `rag_chunks` 表上做 TF cosine 全文检索
- 新增 PG embedding 向量 cosine fallback 搜索路径：当 Milvus 不可用且 `embed_fn` 可用时，用 PG 中存储的 embedding JSONB 做向量 cosine
- 新增 `RetrievalConfig` dataclass 作为查询参数化容器，支持 `search_mode`（vector/keyword/hybrid）、权重、top_k、graph 参数等
- `HybridStore.search()` 方法接受 `RetrievalConfig`，按 `search_mode` 选择检索路径组合
- `HybridStore.mode()` 方法新增 `"tfidf"` 和 `"pg_embedding"` 模式
- 新增 asyncio.Semaphore 并发控制，覆盖检索 I/O 和 embedding 批量调用
- RRF 融合权重从 settings 固定值改为 RetrievalConfig 动态参数
- ~~ES index mapping 增加中文分词 analyzer 配置~~ ← **已撤销**：方案要求完全移除 ES（由 `rag-milvus-bm25-migration` 提案执行）

## Capabilities

### New Capabilities

- `rag-retrieval-enhancement`: RAG 检索增强——PG TF cosine fallback + PG embedding cosine fallback + RetrievalConfig 查询参数化 + asyncio Semaphore 并发控制

### Modified Capabilities

（无——HybridStore 的行为变更属于实现细节，不影响 spec 级别的 requirement）

## Impact

- **修改文件**: `backend/app/infra/hybrid.py`（TF cosine fallback + PG embedding fallback + RetrievalConfig + 并发控制 + tfidf/pg_embedding mode）、`backend/app/rag/rag_engine.py`（query 方法增加 retrieval_config 参数）、`backend/app/services/rag_service.py`（search 方法增加 retrieval_config 参数）
- **新增函数**: `_search_tfidf_fallback()`、`_tokenize()`、`_compute_tf()`、`_cosine_similarity()`、`_search_pg_embedding_fallback()`、`_vec_norm()`、`_vec_cosine()`、`_get_query_semaphore()`、`_run_query_io()`
- **依赖**: `rag-overhaul-foundation` 提案（`rag_search_concurrency`、`rag_embed_concurrency` 配置项）
- **后续提案**: `rag-milvus-bm25-migration` 提案在本提案基础上执行 Milvus native BM25 + ES 移除 + RetrievalConfig 图检索参数扩充
