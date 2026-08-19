## Context

AChat RAG 系统现有 `RAGEngine.query_with_history()` 接受 `history` 参数，内部通过 `LLMRewriter` 做 LLM 指代消解和多查询变体生成。方案决策点 9 确认完全删除 Rewriter——Agent 自身拥有完整对话上下文，构造的搜索词比 Rewriter 更精准。

同时，后续子提案（chunking presets、parser registry、retrieval enhancement、graph build task、eval system、milvus-bm25-migration）共享一组 DB schema 扩展和配置项。本提案先把这层基石打好。

方案后续更新（决策点 2 + 12）确认完全移除 ES，BM25 改用 Milvus native BM25。本提案同步调整：删除 `rag_bm25_analyzer` 配置项（由 `rag-milvus-bm25-migration` 提案执行）、新增 `milvus_bm25_drop_ratio_search` 配置项、调整配置项默认值对齐 Fidi-Intelli。

## Goals / Non-Goals

**Goals:**
- 删除 `rewriter.py` 及所有引用，移除 `history` 参数链路
- 在 `documents` 和 `rag_chunks` 表上新增列，供后续提案使用
- 新增所有后续提案需要的配置项（OCR、chunking、并发、图谱、eval LLM、Milvus BM25）
- 调整配置项默认值对齐 Fidi-Intelli（`rag_rrf_constant_k` 60、`rag_semantic_weight` 0.7、`rag_keyword_weight` 0.3、`ocr_engine` `"auto"`、并发/重试参数）
- 提供幂等启动迁移脚本

**Non-Goals:**
- 不实现 chunking presets dispatcher（提案 rag-chunking-presets）
- 不实现 OCR 引擎注册表（提案 rag-parser-registry）
- 不实现 TF cosine fallback 或 RetrievalConfig 的使用（提案 rag-retrieval-enhancement）
- 不实现图谱构建任务（提案 rag-graph-build-task）
- 不实现评估系统（提案 rag-eval-system）
- 不实现 Milvus native BM25 / ES 移除（提案 rag-milvus-bm25-migration）
- 不修改前端 UI

## Decisions

### Decision 1: 完全删除 Rewriter 而非保留为可选

**Choice**: 删除 `rewriter.py` 和 `rag_rewrite_enabled` 配置项，不保留为可选功能。

**Rationale**: Rewriter 只能看到 query + history 片段，而 Agent 在调用 `rag_search` 前已有完整对话上下文。Agent 构造的搜索词天然比 Rewriter 更精准。保留为可选会增加代码复杂度且无人使用。

**Alternative considered**: 保留 `rag_rewrite_enabled=False` 作为默认关闭的可选功能。否决——维护两套路径的成本不值得。

### Decision 2: 一次性 DB 迁移而非分步迁移

**Choice**: 所有 schema 变更放在一个迁移脚本中，启动时幂等执行。

**Rationale**: 方案决策点 7 确认一步迁移。涉及的列都是 nullable 或有 default，不会破坏现有数据。分步迁移增加复杂度且无收益。

### Decision 3: 配置项统一在此提案新增 + 默认值对齐 Fidi-Intelli

**Choice**: 所有后续提案需要的配置项（OCR、chunking、并发、图谱、eval LLM、Milvus BM25）统一在此提案新增。同时调整已有配置项默认值对齐 Fidi-Intelli：`rag_rrf_constant_k` 30→60、`rag_semantic_weight` 0.5→0.7、`rag_keyword_weight` 0.5→0.3、`ocr_engine` `"none"`→`"auto"`、`rag_graph_concurrency` 4→5、`rag_graph_neo4j_concurrency` 4→8、`rag_graph_retry_delays` `"60,300,900"`→`"2.0,10.0"`。

**Rationale**: `config.py` 是集中管理文件，分散修改会产生多个 PR 都改 config.py 的冲突。一次性新增所有配置项 + 调整默认值，后续提案只读不改。`rag_bm25_analyzer` 配置项保留但标记为待删除——在 `rag-milvus-bm25-migration` 提案中随 ES 移除一起删除，因为 Milvus analyzer 在 Collection schema 中固定为 `{"type":"chinese"}`。

### Decision 4: `query_with_history` 改名为 `query` 而非保留别名

**Choice**: 直接改名，不保留 `query_with_history` 作为向后兼容别名。

**Rationale**: 这是内部 API，所有调用方都在代码库内。保留别名只会增加维护负担。

## Risks / Trade-offs

- **[Risk] `rag_search` 工具移除 history 后检索质量下降** → Agent 需要自行构造完整搜索词；实际上 Agent 的 LLM 在构造搜索词时比 Rewriter 更优，因为 Agent 有完整上下文
- **[Risk] 一次性新增 ~25 个配置项可能让用户困惑** → 配置项都有合理默认值，不配置也能正常工作；配置项分组有注释
- **[Risk] 迁移脚本在 PG 不可用时启动失败** → 迁移放在 try/except 中，失败只 warn 不阻断启动（与现有 `_migrate_*` 模式一致）

## Migration Plan

1. 迁移脚本 `rag_overhaul_migration.py` 在 `main.py` lifespan 中、RAGService 初始化前调用
2. 使用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（PG 语法）确保幂等
3. 回填：`UPDATE documents SET chunk_preset = 'general' WHERE chunk_preset IS NULL`
4. 回填：`UPDATE documents SET graph_status = 'graph_indexed' WHERE status = 'active' AND graph_status IS NULL`
5. 新增列在 `models.py` 中同步声明，`create_all` 在全新库时会自动建列
6. 回滚：`ALTER TABLE ... DROP COLUMN IF EXISTS`（如需回滚）

## Open Questions

无——所有决策点已在方案文档中确认。

## Post-Implementation Amendments

方案文档后续更新（决策点 2 + 12）确认完全移除 ES，BM25 改用 Milvus native BM25。以下变更在原 implementation 基础上追加：

- `rag_bm25_analyzer` 配置项标记为待删除——由 `rag-milvus-bm25-migration` 提案执行删除
- 新增 `milvus_bm25_drop_ratio_search: float = 0.0` 配置项
- 调整配置项默认值：`rag_rrf_constant_k` 30→60、`rag_semantic_weight` 0.5→0.7、`rag_keyword_weight` 0.5→0.3、`ocr_engine` `"none"`→`"auto"`、`rag_graph_concurrency` 4→5、`rag_graph_neo4j_concurrency` 4→8、`rag_graph_retry_delays` `"60,300,900"`→`"2.0,10.0"`
