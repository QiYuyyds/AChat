## Why

AChat RAG 当前图谱构建是在文档 ingest 时同步 fire-and-forget 触发的（`asyncio.create_task(self._kg_index_fn(doc_hash, chunk_refs))`），没有重试、并发控制、状态追踪。图谱构建失败后静默丢失，用户不知道图谱状态。Fidi-Intelli 有完整的异步图谱构建任务（带状态机、重试、并发控制），AChat 需要移植这套能力。

## What Changes

- 新增 `backend/app/rag/graph_build_task.py`：异步图谱构建任务，带状态机、重试（MAX_EXTRACTION_ATTEMPTS=3）、LLM 并发控制（Semaphore=5）
- 新增 `backend/app/rag/graph_retrieval.py`：图谱检索增强（PPR + entity/triple vector search）
- 新增 `backend/app/rag/milvus_graph_vector_store.py`：图谱实体/三元组 Milvus 向量存储（entity Collection + triple Collection，dense + BM25 sparse）
- `backend/app/graph/extractor.py` 增加批量抽取 `extract_batch(chunks)` 方法 + 并发控制
- `backend/app/graph/types.py` 新增 `TripleRef` 数据结构
- `backend/app/graph/kgstore.py` 的 `search()` 增加 PPR (Personalized PageRank) 支持
- `DocumentService.ingest_version()` 完成后触发 `GraphBuildTask.build()`
- `Document.graph_status` 列管理图谱构建生命周期：`graph_pending` → `graph_building` → `graph_indexed` / `error_graph`
- 文件生命周期状态机 `backend/app/rag/file_lifecycle.py`（`FileStatus` + `DocumentLifecycleManager`）

## Capabilities

### New Capabilities

- `rag-graph-build-task`: 异步图谱构建任务系统——状态机驱动、重试机制、LLM 并发控制、PPR 检索增强、文件生命周期 FSM

### Modified Capabilities

（无——KGStore 的行为变更属于实现细节，不影响现有 spec 的 requirement 级别）

## Impact

- **新增文件**: `backend/app/rag/graph_build_task.py`、`backend/app/rag/graph_retrieval.py`、`backend/app/rag/milvus_graph_vector_store.py`、`backend/app/rag/file_lifecycle.py`（共 4 个文件）
- **修改文件**: `backend/app/graph/extractor.py`（批量抽取 + 并发）、`backend/app/graph/types.py`（TripleRef）、`backend/app/graph/kgstore.py`（PPR 检索）、`backend/app/infra/hybrid.py`（_fetch_kg 改调 GraphRetrieval）、`backend/app/services/document_service.py`（图谱触发 + 生命周期管理）
- **依赖**: `rag-overhaul-foundation` 提案（`Document.graph_status` 列、`rag_graph_*` 配置项）、`rag-retrieval-enhancement` 提案（并发控制 Semaphore 模式）、`rag-milvus-bm25-migration` 提案（Milvus BM25 sparse 字段模式用于 MilvusGraphVectorStore）
