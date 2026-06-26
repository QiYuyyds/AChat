## 1. 创建 graph 模块

- [x] 1.1 创建 `backend/app/graph/__init__.py`，导出 Entity、Relation、GraphSearchResult、ExtractResult、ChunkRef、Extractor、KGStore
- [x] 1.2 创建 `backend/app/graph/types.py`，从 AGI-memory `internal/graph/types.py` 移植数据结构（Entity、Relation、GraphSearchResult、ExtractResult、ChunkRef 及实体/关系类型常量），原样移植不修改
- [x] 1.3 创建 `backend/app/graph/extractor.py`，从 AGI-memory `internal/graph/extractor.py` 移植 Extractor 类（LLM 实体关系抽取器），原样移植不修改——保持同步 `llm_fn` 签名
- [x] 1.4 创建 `backend/app/graph/kgstore.py`，从 AGI-memory `internal/graph/kgstore.py` 移植 KGStore 类，做以下 async 适配：
  - `__init__` 接受 `neo4j.AsyncDriver` 替代 `Neo4jClient`，接受 `Settings` 替代 `APIConfig`
  - `_upsert_entity` / `_upsert_relation` / `delete_document` / `search` / `_search_direct` 中的 `self.neo4j.run_cypher()` 改为 `await self._run_cypher()`（内部用 `async with self._driver.session() as session`）
  - `index_document` 改为 `async def`，内部 `_upsert_entity` / `_upsert_relation` 改为 `await`
  - `delete_document` 改为 `async def`
  - `search` 改为 `async def`，返回 `List[dict]`（含 `pg_id`、`content`、`score`、`entities` 键）而非 `List[GraphSearchResult]`
  - `available()` 检查 `self._driver is not None`
  - 保留 `_search_direct` 降级路径和 APOC Cypher 查询逻辑
  - 保留内部工具函数 `_to_int` / `_to_int64` / `_to_string` / `_to_string_list`

## 2. 修改 HybridStore

- [x] 2.1 在 `backend/app/infra/hybrid.py` 的 `__init__` 中新增 `self._kg_index_fn: Optional[Callable] = None` 和 `self._kg_delete_fn: Optional[Callable] = None` 字段
- [x] 2.2 新增 `set_kg_index_fn(self, fn: Callable)` 和 `set_kg_delete_fn(self, fn: Callable)` setter 方法
- [x] 2.3 在 `index_chunks` 方法末尾（ES index 之后、return 之前）新增 KG 入库调用：构建 `ChunkRef` 列表后以 `asyncio.create_task(self._kg_index_fn(doc_hash, chunk_refs))` fire-and-forget 触发，需导入 `from app.graph.types import ChunkRef`
- [x] 2.4 修复 `_fetch_kg` 中的同步调用 bug：将 `hits = self._kg_search_fn(query, fetch_k) or []` 改为 `hits = (await self._kg_search_fn(query, fetch_k)) or []`

## 3. 修改 RAGService 透传

- [x] 3.1 在 `backend/app/services/rag_service.py` 新增 `set_kg_index_fn(self, fn: Callable)` 方法，委托给 `self._hybrid.set_kg_index_fn(fn)`（`set_kg_backend` 和 `set_kg_delete_fn` 已存在，无需新增）

## 4. 修改 main.py wiring

- [x] 4.1 在 `backend/app/main.py` 新增 `_wire_kg_to_rag(rag_service, neo4j_driver, settings, generate_fn)` 函数：创建 `Extractor(generate_fn)` 和 `KGStore(settings, neo4j_driver, extractor)`，定义 `kg_search`/`kg_index`/`kg_delete` async 闭包，调用 `rag_service.set_kg_backend(kg_search)`、`rag_service.set_kg_index_fn(kg_index)`、`rag_service.set_kg_delete_fn(kg_delete)`，输出日志 `RAG: KG backend wired`
- [x] 4.2 在 `lifespan` 函数的 RAGService 初始化块中（`generate_fn` 注入之后、`await _rag_service.initialize()` 之前），新增 KG wiring 调用：当 `_infrastructure.neo4j_driver` 和 `generate_fn` 均可用时调用 `_wire_kg_to_rag`
- [x] 4.3 在 `_log_startup_dashboard` 中新增 KG 状态行：检查 `_rag_service` 是否已 wire KG（可通过检查 `generate_fn` 和 `_infrastructure.neo4j_driver` 推断），输出 `KG Backend: ✓ wired` 或 `✗ not wired`

## 5. 端到端验证

- [ ] 5.1 启动后端，检查日志中出现 `RAG: KG backend wired`（需 Neo4j 运行 + LLM API Key 配置）
- [ ] 5.2 上传文档后，在 Neo4j Browser 执行 `MATCH (e:Entity) RETURN e.name, e.type, e.doc_hash, e.pg_id LIMIT 10`，验证 `:Entity` 节点已创建
- [ ] 5.3 在 Neo4j Browser 执行 `MATCH (a:Entity)-[r]->(b:Entity) RETURN a.name, type(r), b.name LIMIT 10`，验证关系边已创建
- [ ] 5.4 调用 `rag_search`，检查后端日志中出现 KG 搜索路径参与 RRF 融合（`_fetch_kg` 返回 `ok=True`）
- [ ] 5.5 删除文档后，在 Neo4j Browser 执行 `MATCH (e:Entity {doc_hash: "xxx"}) RETURN count(e)`，验证 Entity 和关系被清理（计数为 0）
- [ ] 5.6 停止 Neo4j 后调用 `rag_search`，验证 KG 路径静默降级（`_fetch_kg` 返回 `ok=False`），Milvus + ES 两路检索正常返回结果
- [x] 5.7 运行现有测试套件 `python -m pytest backend/tests/test_rag_hybrid.py -v`，验证 HybridStore 单元测试仍通过（KG 路径未 wire 时 `_kg_ok()` 返回 `False`，行为不变）
