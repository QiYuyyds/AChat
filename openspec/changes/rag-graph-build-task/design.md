## Context

现有图谱构建在 `HybridStore.index_chunks()` 中以 `asyncio.create_task(self._kg_index_fn(doc_hash, chunk_refs))` fire-and-forget 触发，无重试、无状态追踪、无并发控制。方案决策点 3 要求对齐 Fidi-Intelli 的图谱构建任务。

现有 `KGStore.search()` 使用 APOC `apoc.path.subgraphNodes` 做 1~2 跳遍历。方案要求增加 PPR (Personalized PageRank) 检索增强。

## Goals / Non-Goals

**Goals:**
- 实现异步 GraphBuildTask（状态机 + 重试 + 并发控制）
- 实现 PPR 检索增强
- 实现批量实体抽取 `extract_batch()`
- 实现文件生命周期状态机 `DocumentLifecycleManager`
- 实现 `MilvusGraphVectorStore`（图谱 entity/triple Milvus 向量存储，dense + BM25 sparse）

**Non-Goals:**
- 不修改现有 `KGStore.index_document()` 的 Cypher 逻辑（只是包装在 GraphBuildTask 中）
- 不修改 Neo4j schema（不新增节点/关系类型）
- 不实现前端图谱状态 UI
- 不修改记忆系统的 graph memory

## Decisions

### Decision 1: GraphBuildTask 独立于 HybridStore.index_chunks()

**Choice**: 图谱构建从 `HybridStore.index_chunks()` 中移出，改为在 `DocumentService.ingest_version()` 完成后由 `GraphBuildTask` 独立触发。

**Rationale**: 现有 fire-and-forget 方式无法重试、无法追踪状态。独立任务可以管理完整生命周期。

**Alternative considered**: 在 HybridStore 内部加重试和状态。否决——HybridStore 职责是检索，不应承担图谱构建状态管理。

### Decision 2: PPR 使用 Neo4j GDS 库或 APOC

**Choice**: PPR 优先尝试使用 Neo4j GDS 库的 `gds.pageRank.stream` 或 APOC 的 `apoc.path.subgraphNodes` + scoring。

**Rationale**: Neo4j GDS 库提供原生 PPR 算法。如果 GDS 未安装，降级为 APOC subgraph 遍历 + 手动 scoring。

### Decision 3: 文件生命周期状态机管理 graph_status 而非 status

**Choice**: `DocumentLifecycleManager` 管理两个独立状态字段：`Document.status`（文件解析/索引状态）和 `Document.graph_status`（图谱构建状态）。两者独立流转。

**Rationale**: 图谱构建是异步增强路径，它的状态不应影响文档的解析/索引状态。独立管理避免状态耦合。

### Decision 4: extract_batch 基于现有 extract 方法

**Choice**: `Extractor.extract_batch(chunks)` 内部循环调用 `extract()`，用 Semaphore 控制并发。

**Rationale**: 现有 `extract()` 方法已经成熟（JSON 解析 + 清洗）。batch 版本只是加并发控制，不改变抽取逻辑。

### Decision 5: MilvusGraphVectorStore 为 entity 和 triple 各创建独立 Milvus Collection

**Choice**: 对齐 Fidi-Intelli `milvus_graph_vector_store.py`，图谱的 entity 和 triple 各有独立的 Milvus Collection，同样具备 dense embedding + BM25 sparse 字段（`content` 字段 `enable_analyzer=True` + `analyzer_params={"type":"chinese"}` + `Function(BM25)` + `SPARSE_FLOAT_VECTOR` + `SPARSE_INVERTED_INDEX`）。triple Collection 额外有 `source_id` / `target_id` 字段。

**Rationale**: 方案 §3.5.4 要求图谱向量存储对齐 Fidi-Intelli。entity/triple 向量召回 + BM25 全文检索能力是图谱检索增强的关键组成部分——检索时先从 Milvus 向量召回 entity/triple，再从 Neo4j PPR 扩散。

**Alternative considered**: 只用 Neo4j 做图谱检索（无 Milvus 向量召回）。否决——纯图遍历无法做语义相似度召回，只有精确匹配实体名才能命中。

**Dependency**: `rag-milvus-bm25-migration` 提案先落地 Milvus BM25 sparse 字段模式（Collection schema + Function(BM25) + SPARSE_INVERTED_INDEX），`MilvusGraphVectorStore` 复用同样的 schema 模式。

## Risks / Trade-offs

- **[Risk] Neo4j GDS 库未安装时 PPR 不可用** → 降级为 APOC subgraph 遍历 + 手动 scoring
- **[Risk] 图谱构建任务失败后文档仍可检索** → 这是设计意图（图谱是增强路径），`error_graph` 状态记录便于用户知晓
- **[Risk] 并发 LLM 调用导致 API 限流** → Semaphore 默认 5 并发，可配置调整

## Open Questions

无。
