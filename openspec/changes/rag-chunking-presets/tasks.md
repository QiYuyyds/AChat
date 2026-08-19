## 1. Chunking 包结构和共享工具

- [x] 1.1 创建 `backend/app/rag/chunking/__init__.py`
- [x] 1.2 创建 `backend/app/rag/chunking/nlp.py`：token 计数函数（`count_tokens(text)`）和文本切分辅助函数
- [x] 1.3 创建 `backend/app/rag/chunking/utils/__init__.py`
- [x] 1.4 创建 `backend/app/rag/chunking/utils/md_parser_utils.py`：Markdown 标题树解析、代码块保护、表格保护工具函数
- [x] 1.5 创建 `backend/app/rag/chunking/utils/semantic_utils.py`：句子边界检测、嵌入聚类辅助函数
- [x] 1.6 创建 `backend/app/rag/chunking/utils/table_utils.py`：表格结构识别与保护

## 2. Preset 定义和调度器

- [x] 2.1 创建 `backend/app/rag/chunking/presets.py`：定义 `CHUNK_PRESETS` 字典（4 种 preset 的 label + description）、`normalize_chunk_preset_id(preset_id) -> str`、`resolve_chunk_processing_params(preset_id, config) -> dict`
- [x] 2.2 创建 `backend/app/rag/chunking/dispatcher.py`：`chunk_markdown(content, preset_id, config) -> list[str]` 调度入口，按 preset_id 路由到对应 parser

## 3. Preset 实现

- [x] 3.1 创建 `backend/app/rag/chunking/parsers/__init__.py`
- [x] 3.2 创建 `backend/app/rag/chunking/parsers/general.py`：`chunk_markdown(content, config) -> list[str]`，内部委托给 `RecursiveSplitter`
- [x] 3.3 创建 `backend/app/rag/chunking/parsers/qa.py`：`chunk_markdown(content, config) -> list[str]`，正则匹配 QA 结构，非 QA 内容回退 general
- [x] 3.4 创建 `backend/app/rag/chunking/parsers/semantic.py`：`chunk_markdown(content, config, embed_fn=None) -> list[str]`，句子切分 + 嵌入聚类，embed_fn 不可用时回退 general
- [x] 3.5 创建 `backend/app/rag/chunking/parsers/separator.py`：`chunk_markdown(content, config) -> list[str]`，按配置分隔符切分

## 4. RAGEngine 接入

- [x] 4.1 `backend/app/rag/rag_engine.py`：`ingest()` 方法新增 `preset_id: str = ""` 参数，默认使用 `settings.rag_chunk_preset`
- [x] 4.2 `backend/app/rag/rag_engine.py`：`ingest()` 内部将 `parent_splitter`/`child_splitter` 的调用改为通过 `ChunkDispatcher.chunk_markdown(content, preset_id, config)` 调度
- [x] 4.3 `backend/app/rag/rag_engine.py`：semantic preset 需要时传入 `embed_fn`

## 5. DocumentService 接入

- [x] 5.1 `backend/app/services/document_service.py`：`upload_file()` 和 `ingest_version()` 传递 `preset_id` 到 `RAGEngine.ingest()`
- [x] 5.2 `backend/app/services/document_service.py`：ingest 后将 `preset_id` 写入 `Document.chunk_preset` 列

## 6. 验证

- [x] 6.1 `ruff check .` 通过
- [x] 6.2 `pytest` 通过
- [x] 6.3 手动测试：用 general preset ingest 一个笔记文档，确认分块结果与改动前一致
- [x] 6.4 手动测试：用 qa preset ingest 一份面经文档，确认 QA 对被正确抽取
