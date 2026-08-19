## 1. DB Schema 扩展

- [x] 1.1 `backend/app/db/models.py`: `Document` 模型新增 `chunk_preset: Mapped[str] = mapped_column(String(32), nullable=False, default="general")` 列
- [x] 1.2 `backend/app/db/models.py`: `Document` 模型新增 `graph_status: Mapped[str | None] = mapped_column(String(16), nullable=True)` 列
- [x] 1.3 `backend/app/db/models.py`: `RagChunk` 模型新增 `chunk_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)` 列
- [x] 1.4 `backend/app/db/models.py`: `RagChunk` 模型新增 `start_char_pos: Mapped[int | None] = mapped_column(Integer, nullable=True)` 列
- [x] 1.5 `backend/app/db/models.py`: `RagChunk` 模型新增 `end_char_pos: Mapped[int | None] = mapped_column(Integer, nullable=True)` 列

## 2. 迁移脚本

- [x] 2.1 创建 `backend/app/db/migrations/rag_overhaul_migration.py`，实现 `migrate_rag_overhaul()` async 函数
- [x] 2.2 迁移函数使用 `ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_preset VARCHAR(32) DEFAULT 'general'` 等 PG 幂等语法
- [x] 2.3 迁移函数回填 `documents.chunk_preset = 'general' WHERE chunk_preset IS NULL`
- [x] 2.4 迁移函数回填 `documents.graph_status = 'graph_indexed' WHERE status = 'active' AND graph_status IS NULL`
- [x] 2.5 迁移函数对 `rag_chunks` 表执行 `ADD COLUMN IF NOT EXISTS` 三列
- [x] 2.6 `backend/app/main.py` lifespan 中、RAGService 初始化前调用 `migrate_rag_overhaul()`，try/except 不阻断启动

## 3. 配置项变更

- [x] 3.1 `backend/app/config.py`: 删除 `rag_rewrite_enabled` 和 `rag_rewrite_num_queries` 配置项
- [x] 3.2 `backend/app/config.py`: 新增 OCR 配置项 (`ocr_engine`, `ocr_rapid_ocr_path`, `ocr_mineru_url`, `ocr_mineru_official_key`, `ocr_deepseek_ocr_key`, `ocr_paddleocr_key`)
- [x] 3.3 `backend/app/config.py`: 新增 chunking 配置项 (`rag_chunk_preset`, `rag_chunk_parser_config`)
- [~] 3.4 `backend/app/config.py`: 新增 BM25 配置项 (`rag_bm25_analyzer`)
  <!-- superseded by rag-milvus-bm25-migration: rag_bm25_analyzer will be deleted (Milvus analyzer fixed in schema) -->
- [x] 3.5 `backend/app/config.py`: 新增并发控制配置项 (`rag_embed_concurrency`, `rag_search_concurrency`, `rag_graph_concurrency`, `rag_graph_neo4j_concurrency`)
- [x] 3.5a `backend/app/config.py`: 调整 `rag_graph_concurrency` 默认值 4→5
- [x] 3.5b `backend/app/config.py`: 调整 `rag_graph_neo4j_concurrency` 默认值 4→8
- [x] 3.5c `backend/app/config.py`: 新增 `milvus_bm25_drop_ratio_search: float = 0.0` 配置项
- [x] 3.8 `backend/app/config.py`: 调整 `rag_rrf_constant_k` 默认值 30→60
- [x] 3.9 `backend/app/config.py`: 调整 `rag_semantic_weight` 默认值 0.5→0.7
- [x] 3.10 `backend/app/config.py`: 调整 `rag_keyword_weight` 默认值 0.5→0.3
- [x] 3.11 `backend/app/config.py`: 调整 `ocr_engine` 默认值 `"none"`→`"auto"`
- [x] 3.12 `backend/app/config.py`: 调整 `rag_graph_retry_delays` 默认值 `"60,300,900"`→`"2.0,10.0"`
- [x] 3.6 `backend/app/config.py`: 新增图谱配置项 (`rag_graph_auto_build`, `rag_graph_max_extraction_attempts`, `rag_graph_retry_delays`)
- [x] 3.7 `backend/app/config.py`: 新增 eval LLM 配置项 (`eval_llm_api_key`, `eval_llm_api_url`, `eval_llm_model`, `eval_dataset_llm_api_key`, `eval_dataset_llm_api_url`, `eval_dataset_llm_model`)

## 4. 删除 Query Rewriter

- [x] 4.1 删除 `backend/app/rag/rewriter.py` 文件
- [x] 4.2 `backend/app/rag/rag_engine.py`: 删除 `from app.rag.rewriter import HistoryMessage, LLMRewriter` import
- [x] 4.3 `backend/app/rag/rag_engine.py`: 删除 `self._rewriter` 属性和 `set_rewriter()` 方法
- [x] 4.4 `backend/app/rag/rag_engine.py`: `query_with_history()` 改名为 `query()`，移除 `history` 参数，删除 rewriter 调用逻辑，queries 直接为 `[question]`
- [x] 4.5 `backend/app/rag/rag_engine.py`: `query()` 方法直接调用 `self._hybrid.search_multi([question], top_k, user_id=user_id)`
- [x] 4.6 `backend/app/services/rag_service.py`: 删除 `from app.rag.rewriter import HistoryMessage, LLMRewriter` import
- [x] 4.7 `backend/app/services/rag_service.py`: `set_generate_fn()` 中删除 rewriter 装配逻辑（`if self.settings.rag_rewrite_enabled` 分支）
- [x] 4.8 `backend/app/services/rag_service.py`: `search()` 方法签名移除 `history` 参数，改为 `async def search(self, query: str, *, user_id=None) -> tuple[str, list[dict]]`
- [x] 4.9 `backend/app/services/rag_service.py`: `search()` 内部调用改为 `await self._engine.query(query, user_id=user_id)`
- [x] 4.10 `backend/app/main.py`: 确认 `set_generate_fn()` 中不再有 rewriter 装配（由 4.7 覆盖）

## 5. 调用方适配

- [x] 5.1 `backend/app/tools/memory_rag.py`: `rag_search_handler` 中 `_rag_service.search(query, user_id=ctx.user_id)` 确认不传 history（当前代码已不传 history，确认无变更需要）
- [x] 5.2 `backend/app/services/rag_service.py`: `initialize()` 方法中日志行删除 `rewrite=` 参数引用
- [x] 5.3 全局搜索确认无其他 `query_with_history` 或 `HistoryMessage` 的引用残留

## 6. Chunk Metadata 填充

- [x] 6.1 `backend/app/rag/rag_engine.py`: `ingest()` 中计算每个 chunk 的 `token_count`（用 `nlp.count_tokens`）和 `char_positions`（用 `_locate_chunks` 增量搜索定位）
- [x] 6.2 `backend/app/infra/hybrid.py`: `index_chunks()` 新增 `token_counts` 和 `char_positions` 可选参数，创建 `RagChunk` 时填充 `chunk_token_count` / `start_char_pos` / `end_char_pos` 列
- [x] 6.3 `backend/app/rag/rag_engine.py`: 新增 `_locate_chunks(source, chunks)` 辅助函数，用增量 `str.find` 定位每个 chunk 在原文中的 (start, end) 字符位置

## 7. 验证

- [x] 7.1 `ruff check .` 通过
- [x] 7.2 `pytest` 通过（特别注意 RAG 相关测试）
- [x] 7.3 后端启动确认迁移脚本幂等执行无报错
- [x] 7.4 确认 `rag_search` 工具正常工作（无 history 传递）
- [x] 7.5 确认 ingest 后 `rag_chunks` 表中 `chunk_token_count` / `start_char_pos` / `end_char_pos` 列被正确填充
