## 1. PG TF cosine Fallback

- [x] 1.1 `backend/app/infra/hybrid.py`: 新增 `_tokenize(text) -> list[str]` 函数（简单分词，支持中文按字符切分）
- [x] 1.2 `backend/app/infra/hybrid.py`: 新增 `_compute_tf(tokens) -> dict[str, float]` 函数（计算词频向量）
- [x] 1.3 `backend/app/infra/hybrid.py`: 新增 `_cosine_similarity(tf1, tf2) -> float` 函数（计算两个 TF 向量的余弦相似度）
- [x] 1.4 `backend/app/infra/hybrid.py`: 新增 `_search_tfidf_fallback(query, top_k, user_id) -> _PathHits` 方法，从 PG 拉取 chunks（安全上限 5000 行）做 TF cosine 排序

## 2. HybridStore 降级链

- [x] 2.1 `backend/app/infra/hybrid.py`: `mode()` 方法新增 `"tfidf"` 返回值（Milvus 和 ES 都不可用但有 PG chunks）
- [x] 2.2 `backend/app/infra/hybrid.py`: `search()` 方法新增 tfidf 模式分支，调用 `_search_tfidf_fallback()`
- [x] 2.3 `backend/app/infra/hybrid.py`: `_search_keyword()` 路径在 ES 不可用时回退到 `_search_tfidf_fallback()`
  <!-- superseded by rag-milvus-bm25-migration: ES removed, keyword path becomes Milvus BM25 -->
- [~] 2.4 `backend/app/infra/hybrid.py`: `_search_hybrid()` 中 ES path 不可用时用 TF cosine path 替代
  <!-- superseded by rag-milvus-bm25-migration: ES removed, hybrid uses Milvus WeightedRanker -->

## 3. RetrievalConfig 查询参数化

- [x] 3.1 `backend/app/infra/hybrid.py`: 新增 `RetrievalConfig` dataclass（`search_mode`, `final_top_k`, `similarity_threshold`, `bm25_top_k`, `vector_weight`, `bm25_weight`, `kg_weight`, `use_graph_retrieval`, `graph_entity_top_k`, `graph_expand_depth`, `bm25_drop_ratio_search`, `include_distances`）
- [x] 3.2 `backend/app/infra/hybrid.py`: `search()` 方法新增 `retrieval_config: RetrievalConfig | None = None` 参数
- [x] 3.3 `backend/app/infra/hybrid.py`: `search()` 按 `retrieval_config.search_mode` 选择检索路径组合（vector/keyword/hybrid）
- [x] 3.4 `backend/app/infra/hybrid.py`: RRF 融合权重来源从 settings 固定值改为 RetrievalConfig 动态参数（有 config 用 config，无 config 用 settings 默认值）
- [x] 3.5 `backend/app/rag/rag_engine.py`: `query()` 方法新增 `retrieval_config` 参数透传给 HybridStore
- [x] 3.6 `backend/app/services/rag_service.py`: `search()` 方法新增 `retrieval_config` 参数透传给 RAGEngine

## 4. ES Analyzer 增强

- [~] 4.1 `backend/app/main.py`: `_wire_es_to_rag()` 中 ES index mapping 增加 `analyzer` 配置
  <!-- superseded by rag-milvus-bm25-migration: ES completely removed -->
- [~] 4.2 `backend/app/main.py`: 根据 `settings.rag_bm25_analyzer` 配置 content 字段的 analyzer（standard/ik_max_word/smartcn）
  <!-- superseded by rag-milvus-bm25-migration: ES completely removed, rag_bm25_analyzer deleted -->

## 5. 并发控制

- [x] 5.1 `backend/app/infra/hybrid.py`: 新增 `_query_semaphore_refs: dict[int, tuple]` 全局字典
- [x] 5.2 `backend/app/infra/hybrid.py`: 新增 `_get_query_semaphore(limit) -> asyncio.Semaphore` 函数（weakref + loop-scoped）
- [x] 5.3 `backend/app/infra/hybrid.py`: 新增 `_run_query_io(func, *args, **kwargs)` 函数：Semaphore 控制并发的 `asyncio.to_thread` 调用
- [x] 5.4 `backend/app/infra/hybrid.py`: `_fetch_milvus()` 和 `_fetch_es()` 中的阻塞 I/O 通过 `_run_query_io()` 包装
  <!-- _fetch_es() will be removed by rag-milvus-bm25-migration -->
- [x] 5.5 `backend/app/rag/rag_engine.py`: `ingest()` 方法中 embedding 批量调用增加 Semaphore 并发控制（`rag_embed_concurrency`）

## 6. PG Embedding 向量 Cosine Fallback（Milvus 不可用时）

- [x] 6.1 `backend/app/infra/hybrid.py`: 新增 `_vec_norm(v) -> float` 和 `_vec_cosine(query, query_norm, doc) -> float` 向量辅助函数
- [x] 6.2 `backend/app/infra/hybrid.py`: 新增 `_search_pg_embedding_fallback(query, top_k, user_id) -> _PathHits` 方法，从 PG 拉 chunks 的 embedding JSONB，用 query embedding 做向量 cosine 排序
- [x] 6.3 `backend/app/infra/hybrid.py`: 新增 `_search_pg_embedding(query, top_k, user_id) -> list[HybridResult]` 方法，封装 `_search_pg_embedding_fallback` 返回 HybridResult 列表
- [x] 6.4 `backend/app/infra/hybrid.py`: `_search_semantic()` 在 Milvus 不可用时 fallback 到 `_search_pg_embedding()`（当 `embed_fn` 可用时）
- [x] 6.5 `backend/app/infra/hybrid.py`: `_search_hybrid()` 中 Milvus path 不可用时尝试 PG embedding cosine fallback，使 hybrid 模式仍能做 2-way RRF（PG embedding + BM25）
- [x] 6.6 `backend/app/infra/hybrid.py`: `_resolve_mode()` 中 vector 模式在 Milvus 不可用时优先返回 `"pg_embedding"`（当 `embed_fn` 可用），否则才退到 `"tfidf"`
- [x] 6.7 `backend/app/infra/hybrid.py`: `search()` 方法新增 `"pg_embedding"` 模式分支
- [x] 6.8 `backend/app/infra/hybrid.py`: hybrid RRF 融合中 `milvus_path` 引用改为 `semantic_path`（兼容 Milvus 和 PG embedding fallback 两种来源）

## 7. _tfidf_ok 修复

- [x] 7.1 `backend/app/infra/hybrid.py`: `HybridStore.__init__` 新增 `self._has_chunks: bool = False` 实例属性
- [x] 7.2 `backend/app/infra/hybrid.py`: `_tfidf_ok()` 改为返回 `self._has_chunks` 而非硬编码 `True`
- [x] 7.3 `backend/app/infra/hybrid.py`: `index_chunks()` 成功写入 PG 后设置 `self._has_chunks = True`
- [x] 7.4 `backend/app/infra/hybrid.py`: 新增 `check_pg_chunks()` 异步方法，启动时检查 PG 是否有 chunks
- [x] 7.5 `backend/app/services/rag_service.py`: `initialize()` 中检测到已有 chunks 时同步设置 `self._hybrid._has_chunks = True`

## 8. 验证

- [x] 8.1 `ruff check .` 通过
- [x] 8.2 `pytest` 通过（RAG 相关测试全部通过；5 个预存在失败与本次改动无关）
- [x] 8.3 手动测试：Milvus 不可用时检索仍返回结果（TF cosine fallback）
  <!-- verified by TestTfidfFallback::test_tfidf_fallback_returns_results -->
- [x] 8.4 手动测试：传 RetrievalConfig(search_mode="vector") 确认只走 Milvus 路径
  <!-- verified by TestRetrievalConfigVectorMode::test_vector_mode_uses_semantic_only -->
- [x] 8.5 手动测试：高并发检索不导致 Milvus 过载
  <!-- verified by TestConcurrencyControl::test_semaphore_limits_concurrency + test_semaphore_limits_concurrency_with_milvus_search -->
- [x] 8.6 手动测试：Milvus 不可用时检索仍返回语义结果（PG embedding cosine fallback）
  <!-- verified by TestPgEmbeddingFallback::test_pg_embedding_fallback_returns_results + test_semantic_falls_back_to_pg_embedding -->
- [x] 8.7 手动测试：Milvus 不可用且无 embed_fn 时 hybrid 模式仍返回结果（TF cosine fallback）
  <!-- verified by TestHybridDegradesToTfidf::test_hybrid_degrades_to_tfidf -->
