## 1. Milvus Collection Schema 变更

- [x] 1.1 `backend/app/infra/hybrid.py`: 定义 Milvus Collection 新 schema 常量（`CONTENT_FIELD`, `CONTENT_SPARSE_FIELD`, `CONTENT_ANALYZER_PARAMS`, `VECTOR_METRIC_TYPE`）
- [x] 1.2 `backend/app/infra/hybrid.py`: 定义 `FieldSchema` 列表：`id` (VARCHAR PK) + `content` (VARCHAR, enable_analyzer=True, analyzer_params={"type":"chinese"}) + `chunk_id` (VARCHAR) + `file_id` (VARCHAR) + `chunk_index` (INT64) + `embedding` (FLOAT_VECTOR) + `content_sparse` (SPARSE_FLOAT_VECTOR)
- [x] 1.3 `backend/app/infra/hybrid.py`: 注册 `Function(name="content_bm25", input_field_names=["content"], output_field_names=["content_sparse"], function_type=FunctionType.BM25)`
- [x] 1.4 `backend/app/infra/hybrid.py`: 创建双索引——`embedding` 上 `IVF_FLAT` + `COSINE`；`content_sparse` 上 `SPARSE_INVERTED_INDEX` + `DAAT_MAXSCORE` + `BM25` metric
- [x] 1.5 `backend/app/infra/hybrid.py`: Collection 初始化逻辑改为 drop + recreate（检测旧 schema 不含 `content_sparse` 字段时 drop 重建）

> Schema 定义和 Collection 初始化逻辑在 `main.py:_wire_milvus_to_rag()` 的 `_ensure_collection()` 内函数中实现

## 2. 数据清理

- [x] 2.1 `backend/app/db/migrations/rag_overhaul_migration.py` 或新迁移脚本: `DELETE FROM rag_chunks` 清空 PG 中所有 chunk 数据
- [x] 2.2 确认 Milvus Collection drop + recreate 后为空（由 1.5 覆盖）
- [x] 2.3 迁移脚本执行前打 warning 日志：`"WARNING: RAG data will be deleted. Existing documents need to be re-uploaded."`

## 3. Milvus Native BM25 搜索

- [x] 3.1 `backend/app/infra/hybrid.py`: 新增 `async def _search_milvus_bm25(self, query_text: str, top_k: int, *, drop_ratio: float = 0.0, user_id: str | None = None) -> _PathHits` 方法
- [x] 3.2 BM25 搜索参数：`{"metric_type": "BM25", "params": {"drop_ratio_search": drop_ratio}}`
- [x] 3.3 BM25 搜索直接传 `data=[query_text]`（Milvus 自动分词），`anns_field=CONTENT_SPARSE_FIELD`
- [x] 3.4 `output_fields=["content", "chunk_id", "file_id", "chunk_index"]`
- [x] 3.5 BM25 搜索 I/O 通过现有 `_run_query_io()` 包装（复用 `rag-retrieval-enhancement` 的并发控制）

## 4. Milvus hybrid_search + WeightedRanker

- [x] 4.1 `backend/app/infra/hybrid.py`: 新增 `async def _search_milvus_hybrid(self, query_text: str, query_embedding: list[float], top_k: int, *, vector_weight: float, bm25_weight: float, drop_ratio: float, user_id: str | None) -> _PathHits` 方法
- [x] 4.2 构建 `AnnSearchRequest`（vector）：`data=[query_embedding]`, `anns_field="embedding"`, `param={"metric_type":"COSINE","params":{"nprobe":10}}`, `limit=recall_top_k`
- [x] 4.3 构建 `AnnSearchRequest`（BM25）：`data=[query_text]`, `anns_field=CONTENT_SPARSE_FIELD`, `param={"metric_type":"BM25","params":{"drop_ratio_search":drop_ratio}}`, `limit=bm25_top_k`
- [x] 4.4 调用 `collection.hybrid_search(reqs=[vector_req, bm25_req], rerank=WeightedRanker(vector_weight, bm25_weight), limit=recall_top_k, output_fields=[...])`
- [x] 4.5 从 pymilvus 导入 `AnnSearchRequest`, `WeightedRanker`, `Function`, `FunctionType`, `DataType` 等

## 5. 图检索 RRF 后融合

- [x] 5.1 `backend/app/infra/hybrid.py`: 新增 `_fuse_chunk_rankings(milvus_results: list, graph_results: list, rrf_k: float, graph_weight: float) -> list` 方法
- [x] 5.2 RRF 公式：`fused_score(d) = 1.0/(rrf_k + rank_chunk(d)) + graph_weight/(rrf_k + rank_graph(d))`
- [x] 5.3 `_search_hybrid()` 方法改为：先调 `_search_milvus_hybrid()` → 如果 `use_graph_retrieval` 则调 graph 检索 → RRF 后融合
- [x] 5.4 graph 检索结果为空时跳过 RRF 后融合，直接返回 Milvus hybrid 结果

## 6. ES 完全移除

- [x] 6.1 `backend/app/main.py`: 删除 `_wire_es_to_rag()` 函数及其调用（行 133 + 行 753+）
- [x] 6.2 `backend/app/infra/factory.py`: 删除 `from elasticsearch import AsyncElasticsearch` + ES 连接初始化（行 84-90）
- [x] 6.3 `backend/app/infra/status.py`: 删除 `elasticsearch` 状态字段（行 12 + 行 21）
- [x] 6.4 `backend/app/infra/hybrid.py`: 删除 `_fetch_es()` 方法
- [x] 6.5 `backend/app/infra/hybrid.py`: 删除 `_search_keyword()` 中的 ES 路径（改为调 `_search_milvus_bm25()`）
- [x] 6.6 `backend/app/infra/hybrid.py`: 删除 `_search_hybrid()` 中的 ES path 逻辑（改为调 `_search_milvus_hybrid()`）
- [x] 6.7 `backend/app/infra/hybrid.py`: 删除 `_resolve_mode()` 中 ES 相关模式判断
- [x] 6.8 `backend/app/infra/hybrid.py`: 删除 `_run_query_io()` 对 `_fetch_es()` 的引用
- [x] 6.9 `backend/app/config.py`: 删除 `rag_bm25_analyzer` 配置项
- [x] 6.10 `backend/app/config.py`: 删除 `es_addresses` 配置项
- [x] 6.11 `backend/app/services/rag_service.py`: 删除 ES 装配逻辑（`_wire_es_to_rag` 调用等）
- [x] 6.12 `docker-compose.infra.yml`: 删除 `elasticsearch` 服务定义
- [x] 6.13 `backend/.env.example`: 删除 `ES_*` 环境变量
- [x] 6.14 确认 `backend/pyproject.toml` 中 `elasticsearch` 依赖已删除（当前已删除）

## 7. RetrievalConfig 图检索参数扩充

- [x] 7.1 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_triple_top_k: int = 10`
- [x] 7.2 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_max_nodes: int = 10000`
- [x] 7.3 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_top_k: int = 20`
- [x] 7.4 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_weight: float = 1.0`
- [x] 7.5 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `ppr_damping: float = 0.85`

## 8. Milvus 版本检查

- [x] 8.1 `backend/app/main.py` `_wire_milvus_to_rag()`: Collection 初始化前检查 Milvus server version >= 2.4
- [x] 8.2 版本不足时 abort Collection 变更 + warn 日志，系统降级为 PG fallback 模式

## 9. 撤销 rag-retrieval-enhancement ES tasks

- [x] 9.1 确认 `rag-retrieval-enhancement` tasks 4.1-4.2 已标记 [~]（已在 `rag-retrieval-enhancement` 提案中完成）
- [x] 9.2 确认 `rag-retrieval-enhancement` tasks 2.3-2.4 已标记/注释（已在此提案的 6.5-6.6 中覆盖）

## 10. 验证

- [x] 10.1 `ruff check .` 通过（核心修改文件全部通过；main.py/factory.py 的 SIM105 为预先存在的 lint）
- [ ] 10.2 `pytest` 通过
- [ ] 10.3 手动测试：Milvus 可用时，`search_mode="keyword"` 走 Milvus native BM25（不调 ES）
- [ ] 10.4 手动测试：Milvus 可用时，`search_mode="hybrid"` 走 `hybrid_search()` + `WeightedRanker`（不调 ES，不做外部 RRF）
- [ ] 10.5 手动测试：`use_graph_retrieval=True` 时，graph 结果与 Milvus hybrid 结果做 RRF 后融合
- [ ] 10.6 手动测试：Milvus 不可用时降级为 PG TF cosine / PG embedding fallback（不报 ES 错误）
- [ ] 10.7 手动测试：后端启动时无 ES 初始化代码、无 ES 连接尝试、无 ES 配置项
- [ ] 10.8 手动测试：重新上传文档后，Milvus Collection 中 `content_sparse` 字段被自动填充（由 `Function(BM25)` 生成）
- [ ] 10.9 手动测试：`docker-compose.infra.yml` 中无 elasticsearch 服务
