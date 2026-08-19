## Why

AChat RAG 检索系统已实现 Milvus WeightedRanker hybrid_search + graph RRF post-fusion，但缺少 Fidi-Intelli 的 `recall_top_k`（召回阶段独立 top_k）和 chunk source 回填能力。当前 `RetrievalConfig` 的 `final_top_k` 控制最终返回数量，但召回阶段（Milvus 检索、BM25 检索）的 top_k 与最终 top_k 耦合，无法独立调优。此外，`HybridResult` 不返回 chunk 的来源文档信息（document_id、version_id、source_path），Agent 无法引用来源。

## What Changes

- **`RetrievalConfig` 新增 `recall_top_k` 参数**：召回阶段（Milvus dense + BM25）独立使用 `recall_top_k`，最终截断使用 `final_top_k`。默认 `recall_top_k = final_top_k * 4`
- **`HybridResult` 新增 `source_info` 字段**：回填 `document_id`、`version_id`、`source_path`、`chunk_idx`、`start_char_pos`、`end_char_pos`，供 Agent 引用来源
- **`_load_chunks_by_ids` 增强**：从 PG 加载 chunk 时 JOIN `documents` 表获取 `source_path`、`title` 等文档级信息
- **`MilvusRetrievalConfig` 对齐**：Milvus hybrid_search 的 `limit` 参数使用 `recall_top_k` 而非 `final_top_k`；`WeightedRanker` 的权重参数从 `RetrievalConfig` 透传
- **`RAGService.search()` 返回结果增强**：透传 `source_info` 到调用方（`rag_search` 工具 / `PromptAssembler`）

## Capabilities

### New Capabilities

- `rag-retrieval-v2`: 检索增强——recall_top_k 独立参数 + chunk source 回填 + MilvusRetrievalConfig 对齐

### Modified Capabilities

- `persistence`: `rag_chunks` 表的 `document_id`、`version_id`、`start_char_pos`、`end_char_pos` 列已在 rag-overhaul-foundation 中新增，本提案利用这些列做 source 回填

## Impact

- **修改文件**: `backend/app/infra/hybrid.py`（RetrievalConfig 新增 recall_top_k + HybridResult 新增 source_info + _search_hybrid/_search_milvus_hybrid 使用 recall_top_k + _load_chunks_by_ids 增强）、`backend/app/rag/rag_engine.py`（query() 透传 recall_top_k）、`backend/app/services/rag_service.py`（search() 透传）、`backend/app/tools/memory_rag.py`（rag_search handler 输出 source_info）
- **无 DB schema 变更**：所有需要的列已在前序提案中新增
- **无新依赖**
- **API 兼容性**: `recall_top_k` 可选，不传时默认 `final_top_k * 4`；`source_info` 是新增字段，不影响现有消费方
