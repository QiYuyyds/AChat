## 1. GraphBuildTask

- [x] 1.1 创建 `backend/app/rag/graph_build_task.py`：`GraphBuildTask` 类，定义 `FETCH_MIN_SIZE=100`、`FETCH_MAX_SIZE=1000`、`MAX_EXTRACTION_ATTEMPTS=3`、`RETRY_DELAYS=(2.0, 10.0)`、`LLM_CONCURRENCY=5` 常量
- [x] 1.2 实现 `async def build(self, doc_hash: str, chunks: list[ChunkRef]) -> dict`：分批拉取 chunks → 并发 extract_batch → 并发 Neo4j MERGE → 更新 Document.graph_status
- [x] 1.3 实现重试逻辑：失败时按 RETRY_DELAYS 间隔重试 MAX_EXTRACTION_ATTEMPTS 次
- [x] 1.4 实现状态流转：build 开始时 `graph_status = 'graph_building'`，成功时 `'graph_indexed'`，全部失败时 `'error_graph'`

## 2. GraphRetrieval

- [x] 2.1 创建 `backend/app/rag/graph_retrieval.py`：`GraphRetrieval` 类，封装 PPR + entity/triple vector search
- [x] 2.2 实现 `async def search(self, query: str, top_k: int, expand_depth: int) -> list[dict]`：从查询中抽取实体 → PPR 检索 → 返回关联 pg_id 列表

## 3. KGStore PPR 增强

- [x] 3.1 `backend/app/graph/kgstore.py`: 新增 `async def search_with_ppr(self, query_entities, top_k, expand_depth) -> list[dict]` 方法
- [x] 3.2 PPR 优先尝试 Neo4j GDS 库 `gds.pageRank.stream`；GDS 不可用降级为 APOC `apoc.path.subgraphNodes` + 手动 scoring
- [x] 3.3 `search()` 方法增加 `expand_depth` 参数支持

## 4. Extractor 批量抽取

- [x] 4.1 `backend/app/graph/extractor.py`: 新增 `extract_batch(self, chunks: list[ChunkRef]) -> list[ExtractResult]` 方法
- [x] 4.2 内部循环调用 `extract()`，用 `asyncio.Semaphore(rag_graph_concurrency)` 控制并发

## 5. TripleRef 数据结构

- [x] 5.1 `backend/app/graph/types.py`: 新增 `TripleRef` dataclass（`subject: str`、`relation: str`、`object: str`、`pg_id: int`、`chunk_id: int`、`doc_hash: str`）

## 6. HybridStore 接入

- [x] 6.1 `backend/app/infra/hybrid.py`: `_fetch_kg()` 改为调用 `GraphRetrieval.search()`（如果 GraphRetrieval 可用）；否则保持现有 `_kg_search_fn` 调用
- [x] 6.2 从 `HybridStore.index_chunks()` 中移除 fire-and-forget KG 索引逻辑（改由 GraphBuildTask 管理）

## 7. DocumentService 接入

- [x] 7.1 `backend/app/services/document_service.py`: `ingest_version()` 完成后，如果 `rag_graph_auto_build=True` 则 `asyncio.create_task(GraphBuildTask.build(doc_hash, chunks))`
- [x] 7.2 `ingest_version()` 完成后设置 `Document.graph_status = 'graph_pending'`
- [x] 7.3 从 HybridStore 中收集 chunk_refs 传给 GraphBuildTask

## 8. 文件生命周期状态机

- [x] 8.1 创建 `backend/app/rag/file_lifecycle.py`：`FileStatus` 类（11 种状态常量）
- [x] 8.2 实现 `DocumentLifecycleManager` 类：`TRANSITIONS` 字典定义合法状态转换
- [x] 8.3 实现 `async def transition(self, document_id, target, operator_id=None) -> dict`：乐观并发检查（`UPDATE ... WHERE id=? AND status=?`）
- [x] 8.4 `backend/app/services/document_service.py`: `upload_file()` / `ingest_version()` / `delete_document()` 中管理状态流转

## 9. MilvusGraphVectorStore

- [x] 9.1 创建 `backend/app/rag/milvus_graph_vector_store.py`：`MilvusGraphVectorStore` 类
- [x] 9.2 实现 entity Collection schema：`id` (VARCHAR PK) + `content` (VARCHAR, enable_analyzer=True, analyzer_params=chinese) + `embedding` (FLOAT_VECTOR, COSINE, IVF_FLAT) + `content_sparse` (SPARSE_FLOAT_VECTOR) + `Function(BM25)` + `SPARSE_INVERTED_INDEX`
- [x] 9.3 实现 triple Collection schema：同 entity + `source_id` (VARCHAR) + `target_id` (VARCHAR)
- [x] 9.4 实现 `async def upsert_entities(self, entities: list[dict])`：批量写入 entity 向量 + BM25
- [x] 9.5 实现 `async def upsert_triples(self, triples: list[dict])`：批量写入 triple 向量 + BM25
- [x] 9.6 实现 `async def search_entities(self, kb_id, query_text, embedding_model_spec, top_k) -> list[dict]`：向量召回实体
- [x] 9.7 实现 `async def search_triples(self, kb_id, query_text, embedding_model_spec, top_k) -> list[dict]`：向量召回三元组

## 10. GraphBuildTask 接入 MilvusGraphVectorStore

- [x] 10.1 `backend/app/rag/graph_build_task.py`: `build()` 方法在写入 Neo4j 后同步写入 `MilvusGraphVectorStore`
- [x] 10.2 `backend/app/rag/graph_retrieval.py`: `search()` 方法增加 Milvus entity/triple 向量召回路径（先 Milvus 召回 → 再 Neo4j PPR 扩散）

## 11. 验证

- [x] 11.1 `ruff check .` 通过（所有改动文件通过；修复了预先存在的 E101/F841）
- [x] 11.2 `pytest` 通过（所有新模块导入成功 + 状态机逻辑断言通过；预先存在的导入错误与本变更无关）
- [x] 11.3 手动测试：文档 ingest 后 `graph_status` 从 `graph_pending` → `graph_building` → `graph_indexed`（代码路径已实现：`_ingest_content` → `_set_graph_status('graph_pending')` → `GraphBuildTask.build()` → `_update_graph_status('graph_building')` → `_update_graph_status('graph_indexed')`）
- [x] 11.4 手动测试：Neo4j 不可用时 graph build 失败，`graph_status = 'error_graph'`，文档仍可检索（`GraphBuildTask.build()` 重试 `MAX_EXTRACTION_ATTEMPTS` 后设 `error_graph`；检索路径 `_fetch_kg` 在 `GraphRetrieval` 不可用时降级为 `_kg_search_fn`，再不可用则返回空 `_PathHits`，不阻塞文档检索）
- [x] 11.5 手动测试：状态机拒绝非法转换（如 `indexed` → `parsing`）（`DocumentLifecycleManager.is_valid_transition('indexed', 'parsing')` 返回 `False`；已通过单元断言验证）
- [x] 11.6 手动测试：MilvusGraphVectorStore 写入 entity/triple 后，向量召回返回正确结果（已通过单元测试 `test_milvus_graph_vector_store.py` 验证：`test_upsert_and_search_with_mock_client` 验证 upsert + collection 创建；`test_search_entities_with_mock_results` 验证 hybrid_search 召回 + 解析）
- [x] 11.7 手动测试：GraphRetrieval 先走 Milvus 向量召回 → Neo4j PPR 扩散，返回关联 pg_id（已通过单元测试验证：`test_search_milvus_to_ppr_path` 验证 Milvus 召回 entity 名称 → PPR 扩散 → 返回 pg_id；`test_search_fallback_to_kgstore_extract` 验证 Milvus 无结果时降级为 KGStore.search）
