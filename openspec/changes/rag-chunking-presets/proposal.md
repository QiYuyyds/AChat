## Why

AChat RAG 当前只有单一的 `RecursiveSplitter` 分块策略，无法针对不同文档类型（笔记、面经 FAQ、论文、特定分隔符导出格式）做差异化分块。Fidi-Intelli 通过 preset 调度器支持多种分块策略，AChat 需要引入同样的能力来提升检索质量。

## What Changes

- 新增 `backend/app/rag/chunking/` 包，包含 4 种 preset 实现：`general`（复用现有 `RecursiveSplitter`）、`qa`（问答结构抽取）、`semantic`（嵌入聚类语义切分）、`separator`（严格分隔符切分）
- 新增 `ChunkDispatcher` 调度入口，根据 `preset_id` 选择对应 parser
- `RAGEngine.ingest()` 方法增加 `preset_id` 参数，通过 `ChunkDispatcher` 调度分块
- `Document` 表的 `chunk_preset` 列（由 `rag-overhaul-foundation` 提案新增）用于记录文档使用的 preset
- 不做 `book` 和 `laws` preset（决策点 8：目标场景无书籍和法规文档）

## Capabilities

### New Capabilities

- `rag-chunking-presets`: RAG 分块策略调度系统——4 种 preset（general/qa/semantic/separator）+ dispatcher 调度入口 + preset 配置

### Modified Capabilities

（无——不修改现有 capability spec 的 requirement）

## Impact

- **新增文件**: `backend/app/rag/chunking/__init__.py`、`presets.py`、`dispatcher.py`、`nlp.py`、`parsers/__init__.py`、`parsers/general.py`、`parsers/qa.py`、`parsers/semantic.py`、`parsers/separator.py`、`utils/__init__.py`、`utils/md_parser_utils.py`、`utils/semantic_utils.py`、`utils/table_utils.py`（共 13 个文件）
- **修改文件**: `backend/app/rag/rag_engine.py`（ingest 方法接入 ChunkDispatcher）、`backend/app/services/document_service.py`（upload_file/ingest_version 传递 preset_id）
- **依赖**: `rag-overhaul-foundation` 提案（`Document.chunk_preset` 列和 `rag_chunk_preset` 配置项）
- **现有 RecursiveSplitter 保持不动**: `general` preset 内部委托给 `RecursiveSplitter`
