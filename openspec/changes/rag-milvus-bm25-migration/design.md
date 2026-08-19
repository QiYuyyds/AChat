## Context

AChat RAG 的 BM25 全文检索当前依赖 Elasticsearch（ES）。方案文档决策点 2 + 12 确认完全移除 ES，BM25 改用 Milvus 2.4+ 原生 BM25。Milvus 原生支持：
- `content` 字段 `enable_analyzer=True` + `analyzer_params={"type": "chinese"}`
- `Function(BM25)` 自动将 `content` 文本转为 `content_sparse` 稀疏向量
- `SPARSE_INVERTED_INDEX` 索引 + `DAAT_MAXSCORE` 算法
- `WeightedRanker` 原生融合 dense + sparse

本提案在 `rag-retrieval-enhancement` 的 PG fallback + RetrievalConfig + 并发控制基础上，执行 Milvus Collection schema 变更、ES 完全移除、融合策略替换。

## Goals / Non-Goals

**Goals:**
- Milvus Collection schema 变更为 dense + BM25 sparse 双字段
- 实现 `_search_milvus_bm25()` 方法（Milvus native BM25 搜索）
- 实现 `hybrid_search()` + `WeightedRanker` 替代外部 RRF 3-way 融合
- 实现图检索结果 RRF 后融合
- 完全移除 ES（代码 + 配置 + Docker + 环境变量）
- 扩充 `RetrievalConfig` 图检索参数

**Non-Goals:**
- 不修改 PG TF cosine fallback（`rag-retrieval-enhancement` 已实现，作为 Milvus 不可用时的降级路径保留）
- 不修改 PG embedding cosine fallback（同上）
- 不修改 Reranker 架构
- 不实现 MilvusGraphVectorStore（`rag-graph-build-task` 提案实现，但复用本提案的 schema 模式）
- 不做数据回填（用户确认：现有 PG chunk 数据全部删除，Milvus Collection drop + recreate）

## Decisions

### Decision 1: Milvus Collection drop + recreate 而非原地迁移

**Choice**: 现有 Milvus Collection 没有 `content` / `content_sparse` 字段，无法原地 ALTER。直接 drop 现有 Collection，用新 schema recreate。现有 PG `rag_chunks` 数据全部删除。

**Rationale**: Milvus 的 Collection schema 变更不支持添加 `enable_analyzer` 和 `Function` 字段。用户确认不需要保留现有数据——目标场景是个人知识库，用户可以重新上传文档。

**Alternative considered**: 保留旧 Collection + 新建 Collection 做双写过渡。否决——增加复杂度且用户确认不需要数据保留。

### Decision 2: `WeightedRanker` 替代外部 RRF 3-way 融合

**Choice**: Dense + Sparse 融合使用 Milvus 原生 `collection.hybrid_search(reqs=[vector_req, bm25_req], rerank=WeightedRanker(vector_weight, bm25_weight))`，不再用外部 RRF 公式。

**Rationale**: Milvus `WeightedRanker` 在引擎内部完成 Dense + Sparse 融合，性能更好且语义更准确。外部 RRF 需要两路独立查询 + 手动融合，多一次网络往返。

**Alternative considered**: 保留外部 RRF 融合。否决——Milvus 原生融合性能更优，且减少代码复杂度。

### Decision 3: 图检索结果用 RRF 后融合

**Choice**: 图检索结果与 Milvus `hybrid_search` 结果的融合仍使用 RRF 公式（因为图检索路径在独立 Collection / Neo4j 中，无法参与 Milvus `WeightedRanker`）。

```
fused_score(d) = (1.0 / (rrf_k + rank_chunk(d)))           # Milvus hybrid 结果
               + (graph_weight / (rrf_k + rank_graph(d)))   # 图检索结果

rrf_k = 60.0  (Fidi-Intelli 默认值，已在 rag-overhaul-foundation 中调整)
```

**Rationale**: 对齐 Fidi-Intelli `_fuse_chunk_rankings`。图检索不在同一 Milvus Collection 中，无法用 `WeightedRanker` 统一融合。

### Decision 4: 删除 `rag_bm25_analyzer` 配置项

**Choice**: 删除 `rag_bm25_analyzer` 配置项。Milvus analyzer 在 Collection schema 中固定为 `{"type":"chinese"}`。

**Rationale**: Milvus 的 analyzer 配置在 Collection 创建时确定，不支持运行时切换。固定为中文 analyzer 适合目标场景（个人知识库，中文为主）。

### Decision 5: 现有 PG `rag_chunks` 数据全部删除

**Choice**: 迁移时 `DELETE FROM rag_chunks` 清空 PG 中所有 chunk 数据。Milvus Collection drop + recreate 后为空。用户重新上传文档时自动重新索引。

**Rationale**: 用户确认。Milvus Collection schema 变更不支持原地改，现有 embedding 和 content 数据无法直接迁移到新 schema（需要重新 embedding + 重新插入 BM25 sparse 字段）。直接清空比做数据迁移简单且不易出错。

## Risks / Trade-offs

- **[Risk] Milvus 版本 < 2.4 不支持 native BM25** → 迁移前检查 Milvus 版本；版本不足时 warn 并保持 PG fallback
- **[Risk] 用户数据丢失** → 用户已确认清空；迁移脚本执行前打 warning 日志
- **[Risk] Milvus `hybrid_search()` 接口变更** → pymilvus 版本锁定 >= 2.4.17
- **[Risk] 图检索 RRF 后融合的 `graph_weight` 配置不当导致图检索结果被淹没** → 默认 `graph_weight=1.0`，用户可通过 `RetrievalConfig` 调整

## Migration Plan

1. 检查 Milvus 版本 >= 2.4（不支持则 abort + warn）
2. 删除 PG `rag_chunks` 数据：`DELETE FROM rag_chunks`
3. 删除 PG `documents` 中的 `status` 相关状态（可选：重置为 `uploaded` 等待重新处理）
4. Drop 现有 Milvus Collection
5. 用新 schema recreate Milvus Collection（dense + BM25 sparse + Function + 双索引）
6. 删除 ES 相关代码、配置、Docker 服务
7. 重启后端确认 RAGService 初始化正常（Milvus Collection 存在、ES 不再被引用）

## Open Questions

无——所有决策点已在方案文档中确认。
