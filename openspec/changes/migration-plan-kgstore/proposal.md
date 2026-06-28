## Why

AChat 的 RAG 混合检索架构设计了三路 RRF 融合（Milvus 语义 + ES 关键词 + Neo4j 知识图谱），但 KG 路径从未被接入——`set_kg_backend` 从未被调用，`_kg_ok()` 永远返回 `False`，KG 搜索路径被跳过。Neo4j 中仅有 GraphMemory 写入的 `:Memory` 节点（对话记忆），缺少从文档中抽取的 `:Entity` 节点。补全 KG 路径可让 RAG 检索覆盖实体关系推理场景，提升知识库的关联召回能力。

## What Changes

- 新增 `backend/app/graph/` 模块：从 AGI-memory 移植 `types.py`（Entity/Relation/GraphSearchResult 数据结构）、`extractor.py`（LLM 实体关系抽取器）、`kgstore.py`（Neo4j 存储与检索，async 适配）、`__init__.py`（模块导出）
- 修改 `hybrid.py`：新增 `_kg_index_fn`/`_kg_delete_fn` 字段及 setter；`index_chunks` 末尾新增 KG 入库调用（fire-and-forget）；`_fetch_kg` 中将同步调用改为 `await` 异步调用
- 修改 `rag_service.py`：新增 `set_kg_index_fn` 透传方法（`set_kg_backend` 和 `set_kg_delete_fn` 已存在）
- 修改 `main.py`：新增 `_wire_kg_to_rag` wiring 函数，在 lifespan 中 Neo4j driver 可用时调用
- 文档摄入时通过 LLM 抽取实体关系写入 Neo4j `:Entity` 节点和动态关系类型边
- 搜索时从查询文本抽取实体，执行 1~2 跳子图遍历，返回关联 chunk 的 `pg_id` 列表参与 RRF 融合
- APOC 不可用时自动降级为直接实体匹配；LLM 不可用时 KG 路径静默跳过，不影响 Milvus + ES 两路检索

## Capabilities

### New Capabilities

- `kg-store`: 知识图谱存储与检索模块——LLM 实体关系抽取（Extractor）+ Neo4j 知识图谱存储（KGStore），支持文档摄入时实体/关系入库、查询时实体匹配与多跳遍历、文档删除时级联清理，所有操作在 Neo4j/LLM 不可用时优雅降级

### Modified Capabilities

（无现有 spec 级别变更——`hybrid.py`/`main.py`/`rag_service.py` 的改动是实现层面的 wiring，不改变已有能力契约的行为规格）

## Impact

- **新增文件**: `backend/app/graph/__init__.py`、`types.py`、`extractor.py`、`kgstore.py`
- **修改文件**: `backend/app/infra/hybrid.py`（新增字段/setter/index 调用/await 修复）、`backend/app/services/rag_service.py`（新增 `set_kg_index_fn` 透传）、`backend/app/main.py`（新增 `_wire_kg_to_rag` + lifespan 调用）
- **依赖**: 复用已有 `neo4j` async driver（与 GraphMemory 共享同一实例），复用已有 `_make_generate_fn` LLM 回调
- **基础设施**: Neo4j 需运行；APOC 插件可选（不可用时降级为直接匹配）
- **性能**: 文档摄入时每个 chunk 触发一次 LLM 抽取（fire-and-forget，不阻塞主入库流程）；搜索时每次查询触发一次 LLM 抽取 + 一次 Cypher 遍历
- **前端/API**: 零改动，`rag_search`/`rag_ingest` 工具契约不变
