## Why

AChat RAG 系统需要向 Fidi-Intelli 架构对齐，引入分块预设、OCR 引擎注册表、图谱构建任务、评估系统等能力。这些子系统共享一组基础设施变更——DB schema 扩展、配置项新增/删除、Query Rewriter 移除——必须先落地，后续各子系统提案才能在其上构建。本提案是整个 RAG 大改的基石层。

## What Changes

- **BREAKING**: 删除 `backend/app/rag/rewriter.py`（LLM Query Rewriter），完全移除指代消解功能。多轮对话上下文理解由 Agent 自身完成——Agent 在调用 `rag_search` 工具前已对对话上下文有完整理解，会构造比 Rewriter 更精准的搜索词
- **BREAKING**: `RAGEngine.query_with_history()` 改名为 `query()`，移除 `history` 参数和 rewriter 调用
- **BREAKING**: `RAGService.search()` 移除 `history` 参数
- 新增 `RetrievalConfig` dataclass 作为查询参数化容器（后续 retrieval-enhancement 提案使用）
- `documents` 表新增 `chunk_preset VARCHAR(32) DEFAULT 'general'` 列和 `graph_status VARCHAR(16)` 列
- `rag_chunks` 表新增 `chunk_token_count INTEGER`、`start_char_pos INTEGER`、`end_char_pos INTEGER` 列
- 新增 ~25 个配置项：OCR 引擎配置、chunking preset 配置、并发控制配置、图谱构建配置、评估系统独立 LLM 配置
- 删除配置项：`rag_rewrite_enabled`、`rag_rewrite_num_queries`；ES 完全移除后删除 `es_addresses`（由 `rag-milvus-bm25-migration` 提案执行）；`rag_bm25_analyzer` 标记为待删除（由 `rag-milvus-bm25-migration` 提案执行，Milvus analyzer 在 Collection schema 中固定为 `{"type":"chinese"}`）
- 新增 `milvus_bm25_drop_ratio_search` 配置项（Milvus BM25 检索时稀疏项丢弃比例，由 `rag-milvus-bm25-migration` 提案使用）
- 调整配置项默认值以对齐 Fidi-Intelli：`rag_rrf_constant_k` 30→60、`rag_semantic_weight` 0.5→0.7、`rag_keyword_weight` 0.5→0.3、`ocr_engine` 默认值 `"none"`→`"auto"`、`rag_graph_concurrency` 4→5、`rag_graph_neo4j_concurrency` 4→8、`rag_graph_retry_delays` `"60,300,900"`→`"2.0,10.0"`
- 新增一次性 DB 迁移脚本 `backend/app/db/migrations/rag_overhaul_migration.py`，启动时幂等执行

## Capabilities

### New Capabilities

- `rag-overhaul-foundation`: RAG 大改基石层——DB schema 迁移、配置项变更、Query Rewriter 删除、RAGEngine/RAGService 签名变更、迁移脚本

### Modified Capabilities

- `persistence`: `documents` 表新增 `chunk_preset` 和 `graph_status` 列；`rag_chunks` 表新增 `chunk_token_count`、`start_char_pos`、`end_char_pos` 列

## Impact

- **删除文件**: `backend/app/rag/rewriter.py`
- **修改文件**: `backend/app/rag/rag_engine.py`（删除 rewriter 装配 + 签名变更）、`backend/app/services/rag_service.py`（删除 rewriter import + search() 签名变更）、`backend/app/config.py`（新增/删除配置项）、`backend/app/db/models.py`（Document/RagChunk 列扩展）、`backend/app/main.py`（删除 rewriter 装配代码 + 迁移调用）、`backend/app/tools/memory_rag.py`（rag_search handler 移除 history 传递）
- **新增文件**: `backend/app/db/migrations/rag_overhaul_migration.py`
- **API 兼容性**: `rag_search` 工具的 `history` 参数被移除，调用方无需传 history
- **DB 迁移**: 启动时自动执行幂等迁移，已有数据回填 `chunk_preset='general'` 和 `graph_status='graph_indexed'`
- **后续提案依赖**: `rag-milvus-bm25-migration` 提案依赖本提案的配置项变更（`milvus_bm25_drop_ratio_search`、调整后的权重默认值等）
