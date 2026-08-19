## Why

AChat 的文件原生记忆系统当前只支持 BM25 关键词检索 + wikilink 图扩展的 RRF 融合搜索（`HybridSearch`），**缺少语义向量检索能力**。这导致：

- **语义召回不足**：用户问"前端框架选型"时，记忆里写的是"项目用了 React 19"，BM25 因关键词不匹配而漏召回
- **跨语言检索断裂**：中文查询无法匹配英文记忆内容，反之亦然
- **与 RAG 子系统不对称**：RAG 子系统已接入 Milvus 向量检索，但记忆系统作为更频繁使用的核心能力反而没有 embedding 检索

学习 ReMe 项目的 embedding pipeline 后，确定采用 ReMe 的检索架构：**BM25 + Vector 两路 RRF 融合，wikilink 降为后处理扩展**（不参与排序竞争，仅给结果附加邻居元数据）。以**轻量级 SQLite BLOB 存储 + 暴力余弦相似度搜索**的路线接入，不引入 FAISS 等外部依赖，保持 file-native 架构的简洁性。

## What Changes

### A. 新增 `VectorIndex` — SQLite BLOB 向量存储与检索

- 新增 `backend/app/memory/search/vector_index.py`，使用 SQLite 表存储 `(path, chunk_idx, chunk_text, embedding BLOB, agent_id, bucket)` 
- 向量以 `struct.pack` 紧凑序列化为 BLOB 存储（`float32` 数组），避免 JSON 开销
- 检索时全量加载向量到内存，暴力计算余弦相似度（记忆文件量级 <10k chunks，延迟可接受）
- 支持 `add()` / `remove()` / `search()` / `clear()` 接口，与 `BM25Index` 对称
- 降级策略：`embed_fn` 不可用时 `VectorIndex` 正常初始化但 `search()` 返回空列表，不阻断核心流程

### B. 新增 `MarkdownChunker` — AST 级 Markdown 分块

- 新增 `backend/app/memory/search/chunker.py`，基于 Markdown 标题层级（`#`/`##`/`###`）进行语义分块
- 学习 ReMe 的 `MarkdownFileChunker` breadcrumb 机制：每个 chunk 携带标题路径上下文（如 `项目配置 > 前端框架 > React 19`）
- 分块策略：按 heading 分段 → 每段附加上级标题作为前缀 → 超长段落按 token 上限（`chunk_size=512`）二次切分
- 短段合并：相邻 chunk 不满 `min_chunk_size=100` 时合并，避免过度碎片化
- frontmatter 字段（name/description/tags）注入每个 chunk 前缀，增强语义信号

### C. 重构 `HybridSearch` — BM25 + Vector 两路 RRF + wikilink 后处理

学习 ReMe 的 `SearchStep` 架构，将搜索流程从当前的"BM25 + wikilink 两路 RRF"重构为：

- **Phase 1（两路并行检索）**：BM25 关键词搜索 + Vector 向量搜索并行执行
- **Phase 2（两路 RRF 融合）**：仅对 BM25 + Vector 结果做 RRF 融合排序，wikilink **不参与排序**
- **Phase 3（wikilink 后处理扩展）**：对融合排序后的 top-k 结果，附加 outlinks/inlinks 邻居元数据（已有 `_build_expansion` 方法保留）

核心变更：
- wikilink 从 RRF 排序参与者**移除**——当前代码中 wikilink 扩展的 paths 直接参与 RRF 排序，会将 BM25 未命中的弱关联文件拉入结果。改为 ReMe 模式后，wikilink 仅作为后处理给已排序结果附加上下文
- `memory_wikilink_weight` 配置项**废弃**（RRF 中不再有 wikilink 分量）
- 新增 `memory_vector_weight` 配置项（默认 0.7，与 ReMe 对齐），`memory_bm25_weight` 默认值从 0.7 调整为 0.3
- 向量搜索对 query 做 embedding 后检索 top-k chunks，按 chunk 所属文件路径聚合（取最高分 chunk 代表文件）
- **降级行为**：`embed_fn` 或 `vector_index` 不可用时，退化为纯 BM25 搜索 + wikilink 后处理扩展

### D. `auto_index` Pipeline 集成 Embedding 生成

- `AutoIndex` 新增可选 `vector_index` 和 `chunker` 依赖
- `index_file()` 在 BM25/wikilink 索引完成后，调用 `chunker.chunk(mem_file)` 分块，对每个 chunk 调用 `embed_fn` 生成向量，写入 `vector_index`
- `remove_file()` 同步清除 vector index 中该文件的所有 chunks
- `full_reindex()` 清空并重建 vector index
- **增量更新**：文件变更时先 `remove(path)` 再 `add()`，与 BM25 的增量策略一致
- embedding 生成失败时仅 warning 日志，不阻断索引流程（单个文件 embedding 失败不应影响其他文件）

### E. `MemoryService` 初始化注入

- `MemoryService.__init__` 新增 `VectorIndex` 实例（`<metadata>/vectors.db`）
- `MemoryService.initialize()` 初始化 vector index + 调用 `auto_index.full_reindex()` 时自动重建向量
- `MemoryService.set_embed_fn()` 新增方法：接收 `_make_embed_fn` 产生的 embedding 函数，注入 `HybridSearch` 和 `AutoIndex`
- `main.py` 启动时：复用 RAG 子系统的 `_make_embed_fn(settings)`，将 `embed_fn` 同时注入 RAG 和 Memory 子系统
- `MemoryService.close()` 关闭 vector index SQLite 连接

### F. 配置项变更

- `memory_vector_weight: float = 0.7` — 向量搜索在 RRF 融合中的权重（与 ReMe 默认值对齐）
- `memory_bm25_weight: float = 0.3` — BM25 权重从 0.7 调整为 0.3（`vector_weight + bm25_weight = 1.0`）
- `memory_wikilink_weight` — **废弃**，wikilink 不再参与 RRF 排序（保留配置项但不再读取，避免 break 已有 `.env`）
- `memory_chunk_size: int = 512` — chunk 最大 token 数（近似按字符数估算）
- `memory_chunk_min_size: int = 100` — chunk 最小合并阈值

## Capabilities

### New Capabilities

- `memory-vector-search`: 记忆系统的向量检索能力，含 SQLite BLOB 向量存储、Markdown AST 分块、暴力余弦相似度搜索、BM25+Vector 两路 RRF 融合、wikilink 后处理扩展

### Modified Capabilities

（无已有 OpenSpec spec 需要修改——记忆系统目前没有独立的 OpenSpec capability spec，本次变更新建 `memory-vector-search` 作为首个记忆检索能力契约）

## Impact

- **新增文件**：
  - `backend/app/memory/search/vector_index.py` — SQLite BLOB 向量存储与暴力检索
  - `backend/app/memory/search/chunker.py` — Markdown AST 语义分块器
- **修改文件**：
  - `backend/app/memory/search/hybrid_search.py` — 重构为 BM25+Vector 两路 RRF + wikilink 后处理
  - `backend/app/memory/search/__init__.py` — 导出 `VectorIndex`、`MarkdownChunker`
  - `backend/app/memory/pipeline/auto_index.py` — 集成 embedding 生成到索引流程
  - `backend/app/memory/memory_service.py` — 初始化注入 vector index + embed_fn
  - `backend/app/config.py` — 新增 3 个 memory 配置项，废弃 1 个
  - `backend/app/main.py` — 将 `_make_embed_fn` 注入 MemoryService
  - `backend/.env.example` — 新增 memory vector 相关配置注释
- **数据库**：新增 `vectors.db` SQLite 文件（`<metadata>/vectors.db`），无 PostgreSQL schema 变更
- **依赖**：无新外部依赖；复用 RAG 子系统已有的 `EMBEDDING_API_KEY` / `httpx` / `embedding_model` 配置
- **降级**：`EMBEDDING_API_KEY` 未配置时，记忆系统退化为纯 BM25 搜索 + wikilink 后处理扩展
- **BREAKING**：`memory_wikilink_weight` 废弃——wikilink 不再参与 RRF 排序。已有 `.env` 中配置了此值的用户不受功能影响（配置项保留但不再读取）
- **风险**：暴力搜索在 chunk 数量极多（>50k）时延迟上升——当前记忆文件量级远低于此阈值；wikilink 从 RRF 移除后，纯图关联的文件不再自动出现在搜索结果中——但通过后处理 expansion 元数据仍可被 LLM 看到
