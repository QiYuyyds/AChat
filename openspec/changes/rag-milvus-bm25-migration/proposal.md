## Why

AChat RAG 的 BM25 全文检索当前依赖 Elasticsearch（ES），方案文档决策点 2 + 12 确认完全移除 ES，BM25 改用 Milvus 2.4+ 原生 BM25（`SPARSE_FLOAT_VECTOR` + `Function(BM25)` + `WeightedRanker`）。这需要 Milvus Collection schema 变更（不支持原地改，需 drop + recreate）、ES 代码/配置/Docker 镜像全面清理、RRF 融合替换为 Milvus `hybrid_search()` + `WeightedRanker`、以及图检索结果的 RRF 后融合。

## What Changes

- **BREAKING**: Milvus Collection schema 变更——新增 `content` 字段（`enable_analyzer=True` + `analyzer_params={"type":"chinese"}`）+ `content_sparse: SPARSE_FLOAT_VECTOR` + `Function(BM25)` 自动从 `content` 文本生成稀疏向量 + `SPARSE_INVERTED_INDEX` + `DAAT_MAXSCORE` 算法
- **BREAKING**: 现有 Milvus Collection drop + recreate（schema 变更不支持原地改）；现有 PG `rag_chunks` 数据全部删除（用户确认），不需要数据回填
- **BREAKING**: 完全移除 Elasticsearch——删除 `_wire_es_to_rag()` + ES 客户端初始化 + `_fetch_es()` / `_search_keyword()` ES 路径 + `infra/factory.py` ES 连接 + `infra/status.py` ES 状态 + docker-compose ES 服务 + `.env.example` ES 环境变量 + `pyproject.toml` ES 依赖
- 删除 `rag_bm25_analyzer` 配置项（Milvus analyzer 在 Collection schema 中固定为 `{"type":"chinese"}`）
- 删除 `es_addresses` 配置项
- 新增 `_search_milvus_bm25()` 方法：直接传 `query_text` 给 `collection.search(anns_field=CONTENT_SPARSE_FIELD, param={"metric_type":"BM25"})`，Milvus 自动分词并计算 BM25
- 新增 Milvus `hybrid_search()` + `WeightedRanker` 替代外部 RRF 3-way 融合：`AnnSearchRequest`(vector) + `AnnSearchRequest`(BM25) → `collection.hybrid_search(rerank=WeightedRanker(vector_weight, bm25_weight))`
- 新增图检索结果 RRF 后融合：Milvus `hybrid_search` 结果 + graph 检索结果用 RRF 公式后融合（因为 graph 检索路径在独立 Collection / Neo4j 中，无法参与 Milvus WeightedRanker）
- 扩充 `RetrievalConfig`：新增 `graph_triple_top_k`、`graph_max_nodes`、`graph_top_k`、`graph_weight`、`ppr_damping`

## Capabilities

### New Capabilities

- `rag-milvus-bm25-migration`: Milvus native BM25 迁移——Collection schema 变更 + ES 完全移除 + WeightedRanker 原生融合 + 图检索 RRF 后融合 + RetrievalConfig 图参数扩充

### Modified Capabilities

- `rag-retrieval-enhancement`: 撤销 ES analyzer 增强 tasks（4.1-4.2 标记 [~]）；降级链从"ES 不可用→TF cosine"改为"Milvus 不可用→TF cosine"

## Impact

- **修改文件**: `backend/app/infra/hybrid.py`（Milvus Collection schema + `_search_milvus_bm25()` + `hybrid_search()` + `WeightedRanker` + 图检索 RRF 后融合 + 删除 `_fetch_es()`/`_search_keyword()` ES 路径 + 删除 `_search_hybrid()` ES path 替代逻辑）、`backend/app/main.py`（删除 `_wire_es_to_rag()` + ES 客户端初始化）、`backend/app/config.py`（删除 `rag_bm25_analyzer` + `es_addresses`）、`backend/app/infra/factory.py`（删除 ES 连接）、`backend/app/infra/status.py`（删除 ES 状态）、`backend/app/services/rag_service.py`（删除 ES 装配）
- **删除文件**: 无（ES 代码内联在 hybrid.py / main.py / factory.py 中，不是独立文件）
- **配置清理**: `docker-compose.infra.yml` 删除 elasticsearch 服务、`backend/.env.example` 删除 ES 环境变量、`backend/pyproject.toml` 确认 elasticsearch 依赖已删除
- **数据清理**: 现有 PG `rag_chunks` 数据全部删除 + Milvus Collection drop + recreate
- **撤销**: `rag-retrieval-enhancement` tasks 4.1-4.2（ES analyzer 增强）标记 [~]
- **依赖**: `rag-overhaul-foundation` 提案（`milvus_bm25_drop_ratio_search` 配置项 + 调整后的权重默认值）、`rag-retrieval-enhancement` 提案（`RetrievalConfig` + 并发控制 + PG fallback）
- **后续提案**: `rag-graph-build-task` 的 `MilvusGraphVectorStore` 复用本提案落地的 Milvus BM25 sparse 字段模式
