## Context

AChat RAG 检索系统当前架构：

```
Query → HybridStore.search()
  ├── _search_milvus_hybrid(query, fetch_k=max(top_k*2, 10), ...)
  ├── _fetch_kg(query, fetch_k, ...)
  └── RRF post-fusion → top_k truncate
```

问题：
1. `fetch_k = max(top_k * 2, 10)` 是硬编码的 2x 倍率，Fidi-Intelli 用独立的 `recall_top_k`（默认 `final_top_k * 4`），召回阶段更宽
2. `HybridResult` 只有 `pg_id`、`content`、`score`、`source`、`parent`，不返回 chunk 所属文档信息
3. Agent 调用 `rag_search` 后无法引用来源文档，只能看到 chunk 内容

Fidi-Intelli 的 `MilvusRetrievalConfig` 模式：
- `recall_top_k`：Milvus dense + BM25 各自召回的数量
- `final_top_k`：RRF 融合后返回的最终数量
- chunk source 回填：每个 chunk 携带 `document_id`、`source_path`、`chunk_idx`、`char_positions`

## Goals / Non-Goals

**Goals:**
- `RetrievalConfig` 新增 `recall_top_k`，召回阶段独立于 `final_top_k`
- `HybridResult` 新增 `source_info` 字段，回填文档级元数据
- `_load_chunks_by_ids` JOIN documents 表获取文档信息
- Milvus hybrid_search 的 `limit` 使用 `recall_top_k`

**Non-Goals:**
- 不修改图谱检索逻辑（`GraphRetrieval` 不变）
- 不修改 chunking 逻辑
- 不修改 embedding 逻辑
- 不修改前端 UI

## Decisions

### Decision 1: `recall_top_k` 默认 `final_top_k * 4`

**Choice**: `RetrievalConfig.recall_top_k` 默认 `None`，当为 `None` 时使用 `final_top_k * 4`（或 `top_k * 4` 当 `final_top_k` 也为 `None`）。

**Rationale**: Fidi-Intelli 用 4x 倍率，召回宽一些确保 RRF 融合后有足够候选。当前硬编码 `max(top_k * 2, 10)` 偏窄。改为 4x + 可配置。

**Alternative considered**: 固定 2x。否决——召回不足导致 RRF 融合效果差。

### Decision 2: `HybridResult.source_info` 字段格式

**Choice**: `HybridResult` 新增 `source_info: dict` 字段，格式：

```python
{
    "document_id": "doc_xxx",
    "version_id": "ver_xxx",
    "source_path": "/folder/file.pdf",
    "title": "file.pdf",
    "chunk_idx": 3,
    "start_char_pos": 120,
    "end_char_pos": 380,
}
```

**Rationale**: 一个 dict 比 6 个独立字段更灵活，后续新增字段不破坏 API。`source_info` 从 `_load_chunks_by_ids` 的 JOIN 结果中填充。

**Alternative considered**: 扁平字段（`HybridResult.document_id` 等）。否决——字段过多，向后兼容性差。

### Decision 3: `_load_chunks_by_ids` JOIN documents 表

**Choice**: `_load_chunks_by_ids` 方法改为 LEFT JOIN `documents` 表，一次性获取 chunk + 文档级信息。

```sql
SELECT rc.*, d.title, d.source_path, d.parent_id
FROM rag_chunks rc
LEFT JOIN documents d ON rc.document_id = d.id
WHERE rc.id IN (...)
```

**Rationale**: 避免二次查询。LEFT JOIN 确保即使 `document_id` 为 NULL（bare-ingest chunks）也不丢失 chunk。

**Alternative considered**: 先查 chunks 再批量查 documents。否决——两次查询不如一次 JOIN。

### Decision 4: Milvus hybrid_search `limit` 使用 `recall_top_k`

**Choice**: `_search_milvus_hybrid` 和 `_search_milvus_bm25` 的 `top_k` 参数改为使用 `recall_top_k`，而非 `fetch_k` 或 `final_top_k`。

**Rationale**: Milvus WeightedRanker 内部做 dense + BM25 融合，`limit` 控制融合后返回数量。用 `recall_top_k` 确保召回阶段足够宽。最终截断由 `_search_hybrid` 末尾的 `top_k` truncation 完成。

**Alternative considered**: 仍用 `fetch_k`。否决——`fetch_k` 是硬编码 2x，无法配置。

## Risks / Trade-offs

- **[Risk] `recall_top_k` 过大导致 Milvus 查询变慢** → 默认 4x 是合理值；用户可通过 `RetrievalConfig` 调小
- **[Risk] JOIN documents 表增加查询开销** → LEFT JOIN 上有索引（`document_id` FK 自带索引），开销可忽略
- **[Risk] `source_info` 序列化到 SSE 事件增大 payload** → `source_info` 是小 dict（< 200 bytes），影响可忽略

## Migration Plan

无 DB schema 变更。纯代码改动，不需要迁移脚本。

## Open Questions

无——所有决策点已在讨论中确认。
