## Context

现有 `HybridStore` 有 3 个检索路径：Milvus semantic、ES BM25、KG graph。Milvus 不可用时缺少语义 fallback。ES 不可用时 keyword 搜索直接消失。方案决策点 2 要求 BM25 改用 Milvus native BM25（不再依赖 ES），决策点 5 要求 PG embedding fallback 保留，决策点 12 要求完全移除 ES。

检索参数（权重、top_k）当前固定在 settings 中，评估系统需要按不同参数组合跑评测。

> **方案更新**：原 Decision 1 保留 ES 作为 BM25 主路径，现已反转——完全移除 ES，BM25 改用 Milvus native BM25。ES 移除和 Milvus native BM25 实现由 `rag-milvus-bm25-migration` 提案执行。本提案保留 PG TF cosine fallback（作为 Milvus 不可用时的降级路径）、RetrievalConfig 查询参数化、并发控制。

## Goals / Non-Goals

**Goals:**
- 实现 PG TF cosine fallback 搜索路径（Milvus 不可用时降级）
- 实现 PG embedding 向量 cosine fallback 搜索路径（Milvus 不可用但 embed_fn 可用时降级）
- 实现 `RetrievalConfig` 查询参数化
- 实现 asyncio Semaphore 并发控制（weakref + loop-scoped 模式）

**Non-Goals:**
- 不实现 Milvus native BM25 / ES 移除（由 `rag-milvus-bm25-migration` 提案执行）
- 不实现 Milvus `hybrid_search()` + `WeightedRanker`（由 `rag-milvus-bm25-migration` 提案执行）
- 不实现图检索 RRF 后融合（由 `rag-milvus-bm25-migration` 提案执行）
- 不修改 Reranker 架构
- 不实现 graph retrieval 参数（graph 相关在提案 rag-graph-build-task 中实现，RetrievalConfig 扩充在 `rag-milvus-bm25-migration` 提案中）

## Decisions

### Decision 1: PG TF cosine 作为 Milvus 不可用时的降级路径

> **REVERSED**: 原决策为“保留 ES 作为 BM25 主路径”。方案文档后续更新（决策点 2 + 12）确认完全移除 ES，BM25 改用 Milvus native BM25。Milvus native BM25 实现由 `rag-milvus-bm25-migration` 提案执行。本提案保留 PG TF cosine fallback 作为 Milvus 不可用时的降级路径。

**Choice**: Milvus 可用时走 Milvus hybrid（dense + BM25），Milvus 不可用时走 PG TF cosine fallback。

**Rationale**: 方案决策点 2 + 12。完全移除 ES，BM25 不再依赖 ES。Milvus 2.4+ 原生支持 BM25 全文检索（`SPARSE_FLOAT_VECTOR` + `Function(BM25)` + `WeightedRanker`）。PG TF cosine fallback 作为 Milvus 不可用时的最后降级路径。

**Alternative considered**: 保留 ES 作为 BM25 主路径。否决——方案明确要求完全移除 ES。

### Decision 2: TF cosine 使用纯 Python 实现

**Choice**: TF cosine fallback 用纯 Python 实现（`_tokenize` → `_compute_tf` → `_cosine_similarity`），不引入额外依赖。

**Rationale**: fallback 路径只在 Milvus 不可用时触发，性能要求不高。引入 scikit-learn 等库增加依赖。安全上限 5000 行防止性能问题。

### Decision 3: RetrievalConfig 作为可选参数

**Choice**: `HybridStore.search()` 和 `RAGService.search()` 接受可选 `retrieval_config` 参数。不传时使用 settings 默认值。

**Rationale**: 向后兼容——现有调用方不需要改动。评估系统可以传不同 config 跑评测。

> **注意**: `RetrievalConfig` 的图检索参数（`graph_triple_top_k`、`graph_max_nodes`、`graph_top_k`、`graph_weight`、`ppr_damping`）在 `rag-milvus-bm25-migration` 提案中扩充。

### Decision 4: 并发控制用 weakref + loop-scoped Semaphore

**Choice**: 对齐 Fidi-Intelli 的 weakref + Semaphore 模式，Semaphore 按 event loop 隔离。

**Rationale**: 避免跨 loop 的 semaphore 释放问题。weakref 确保 loop 销毁后 semaphore 被垃圾回收。

## Risks / Trade-offs

- **[Risk] PG TF cosine 在大数据集上性能差** → 安全上限 5000 行 + warning 日志；用户应优先使用 Milvus
- **[Risk] RetrievalConfig 参数过多导致使用困惑** → 有合理默认值，大多数场景不需要传
- **[Risk] Semaphore 限制并发可能拖慢高并发场景** → 默认值 8 足够大多数场景；可配置调整
