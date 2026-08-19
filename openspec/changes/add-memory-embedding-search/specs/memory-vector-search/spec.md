# Spec: memory-vector-search

## ADDED Requirements

### Requirement: Vector Storage

系统 SHALL 在 `<metadata>/vectors.db` SQLite 数据库中存储记忆文件的 embedding 向量。

- 向量以 `struct.pack` 序列化为 BLOB 存储（float32 数组）
- 每条记录包含：`path`（文件相对路径）、`chunk_idx`（分块索引）、`chunk_text`（分块文本）、`embedding`（向量 BLOB）、`agent_id`、`bucket`
- 主键为 `(path, chunk_idx)`，支持按 path 批量删除和按 agent_id/bucket 过滤检索
- 向量维度由 embedding model 动态决定，首次写入时记录维度，后续写入校验维度一致性
- 系统 MUST 在 `VectorIndex.initialize()` 时创建表和索引
- 系统 MUST 在 `VectorIndex.close()` 时关闭 SQLite 连接

#### Scenario: Vector index initialization

- **WHEN** `MemoryService.initialize()` 被调用且 `vectors.db` 不存在
- **THEN** `VectorIndex.initialize()` 创建 `memory_vectors` 表和 `idx_mv_agent` / `idx_mv_bucket` 索引

#### Scenario: Vector index reinitialization

- **WHEN** `VectorIndex.initialize()` 被调用且 `vectors.db` 已存在
- **THEN** 表和索引保持不变（`CREATE TABLE IF NOT EXISTS`），已有向量数据保留

#### Scenario: Embedding dimension mismatch

- **WHEN** `VectorIndex.add()` 收到的向量维度与已存储向量的维度不一致
- **THEN** 系统 MUST 拒绝写入并记录 warning 日志，不影响已有数据

#### Scenario: Embedding dimension detection

- **WHEN** `VectorIndex` 为空（首次写入）且收到一个 1536 维向量
- **THEN** 系统 MUST 接受该向量并记录维度为 1536，后续写入校验维度 = 1536

### Requirement: Markdown Chunking

系统 SHALL 提供一个 `MarkdownChunker` 将记忆文件按标题层级语义分块。

- 分块依据：`##` 和 `###` 标题行作为分块边界（`#` 视为文件标题，不单独成块）
- 每个 chunk 的文本 MUST 包含：
  1. frontmatter `name` + `description` 作为全局前缀
  2. breadcrumb 标题路径（如 `项目配置 > 前端框架`）
  3. section 正文内容
- 相邻 chunk 文本长度 < `memory_chunk_min_size`（默认 100 字符）时 MUST 合并
- 单个 section 文本长度 > `memory_chunk_size`（默认 512 字符）时 MUST 按段落边界二次切分
- frontmatter 为空时，前缀仅包含 body 内容，不报错

#### Scenario: Simple heading-based chunking

- **WHEN** 一个 Markdown 文件包含 `## 概述` 和 `## 详情` 两个 section
- **THEN** 生成 2 个 chunks，每个 chunk 的 `section_path` 分别为 `概述` 和 `详情`

#### Scenario: Nested heading breadcrumb

- **WHEN** 文件包含 `## 前端框架` 下有 `### React 19` 子标题
- **THEN** `### React 19` 下的 chunk 的 `section_path` 为 `前端框架 > React 19`

#### Scenario: Short section merging

- **WHEN** 两个相邻 section 的文本长度分别为 50 和 200 字符（`min_chunk_size=100`）
- **THEN** 两个 section 合并为 1 个 chunk，文本为两个 section 的拼接

#### Scenario: Frontmatter prefix injection

- **WHEN** 文件 frontmatter `name="React 项目配置"` `description="前端技术栈选型记录"`
- **THEN** 每个 chunk 的文本以 `React 项目配置\n前端技术栈选型记录\n` 开头

#### Scenario: No headings fallback

- **WHEN** Markdown body 不包含任何 `##` 或 `###` 标题
- **THEN** 整个 body 作为单个 chunk，`section_path` 为空字符串

### Requirement: Vector Search

系统 SHALL 支持对记忆文件向量进行余弦相似度搜索。

- `VectorIndex.search(query_embedding, top_k, agent_id, bucket)` MUST 返回 `list[tuple[str, int, float]]`（path, chunk_idx, score）
- 搜索时 MUST 全量加载向量到内存，逐条计算余弦相似度
- 同一 path 的多个 chunk 命中时，返回所有 chunk 命中（由调用方做文件级聚合）
- 搜索结果按 score 降序排列，截取 top_k
- `agent_id` 过滤：包含 `agent_id` 匹配或 `agent_id` 为空（全局）的记录
- `bucket` 过滤：仅返回匹配 bucket 的记录
- `VectorIndex.count()` MUST 返回当前存储的向量总数

#### Scenario: Basic vector search

- **WHEN** `VectorIndex` 中有 5 个 chunks 且 `search(embedding, top_k=3)` 被调用
- **THEN** 返回余弦相似度最高的 3 个 `(path, chunk_idx, score)` 元组

#### Scenario: Agent-scoped vector search

- **WHEN** `search(embedding, top_k=5, agent_id="agent_123")` 被调用
- **THEN** 仅返回 `agent_id="agent_123"` 或 `agent_id=""`（全局）的 chunks

#### Scenario: Empty vector index search

- **WHEN** `VectorIndex` 中没有向量且 `search()` 被调用
- **THEN** 返回空列表，不报错

### Requirement: Two-Way RRF Hybrid Search

`HybridSearch` SHALL 在 `embed_fn` 和 `vector_index` 可用时，执行 BM25 + Vector 两路 RRF 融合搜索。wikilink SHALL NOT 参与 RRF 排序，仅作为后处理扩展附加邻居元数据。

- 两路权重：`bm25_weight`（默认 0.3）、`vector_weight`（默认 0.7），`bm25_weight + vector_weight = 1.0`
- `memory_wikilink_weight` 配置项废弃，`HybridSearch` MUST NOT 读取此值参与排序
- 向量搜索结果 MUST 按文件级聚合：同一 path 的多个 chunk 命中取最高分 chunk 作为该 path 的代表
- 聚合后的 path → rank 参与 RRF 融合，与 BM25 的 rank 统一计算
- RRF 公式：`score = bm25_weight/(k + bm25_rank) + vector_weight/(k + vector_rank)`
- 向量搜索路径未命中 BM25 时，该 path 的 BM25 分量为 0
- BM25 搜索路径未命中向量时，该 path 的 vector 分量为 0
- `SearchResult.scores` dict MUST 包含 `"bm25"`、`"vector"`、`"rrf"` 三个 key（wikilink 不再作为 score key）
- wikilink 邻居元数据 MUST 通过 `_build_expansion()` 后处理附加到搜索结果的 `expansion` 字段，不参与排序

#### Scenario: Two-way fusion with all components

- **WHEN** query 的 embedding 成功生成且 VectorIndex 有数据
- **THEN** 搜索结果包含 BM25 + vector 两路融合的 RRF 分数，`scores` dict 含 `bm25`/`vector`/`rrf` 三个 key

#### Scenario: Vector-only hit

- **WHEN** 一个 path 仅在 vector 搜索中命中（BM25 未命中）
- **THEN** 该 path 的 `scores` 中 `bm25=0`、`vector>0`，仍参与 RRF 排序

#### Scenario: BM25-only hit

- **WHEN** 一个 path 仅在 BM25 搜索中命中（vector 未命中）
- **THEN** 该 path 的 `scores` 中 `bm25>0`、`vector=0`，仍参与 RRF 排序

#### Scenario: Wikilink expansion as post-processing

- **WHEN** 搜索完成后，top-k 结果中的某个 path 有 wikilink 邻居
- **THEN** 该 path 的 `SearchResult.expansion` 字段包含 `outlinks` 和 `inlinks` 邻居元数据（path/name/description/predicate），但邻居文件本身不作为独立搜索结果出现

#### Scenario: Graceful degradation without embed_fn

- **WHEN** `embed_fn` 为 None 或 `vector_index` 为 None 或 `vector_index.count() == 0`
- **THEN** 搜索退化为纯 BM25 搜索，`scores` dict 不含 `vector` key，wikilink 后处理扩展仍执行

#### Scenario: Embedding query generation failure

- **WHEN** query embedding 生成抛出异常（如 API 超时）
- **THEN** 搜索退化为纯 BM25 搜索，记录 warning 日志，不中断搜索流程，wikilink 后处理扩展仍执行

### Requirement: Auto Index Embedding Integration

`AutoIndex` SHALL 在索引文件时同步生成和存储 embedding 向量。

- `index_file()` 在 BM25 + wikilink 索引完成后，MUST 调用 `MarkdownChunker.chunk()` 分块并对每个 chunk 调用 `embed_fn` 生成向量
- 向量写入 `VectorIndex`，携带 path、chunk_idx、chunk_text、agent_id、bucket
- `index_file()` 在写入前 MUST 先 `vector_index.remove(path)` 清除该文件的旧向量
- `remove_file()` MUST 同步清除 vector index 中该文件的所有 chunks
- `full_reindex()` MUST 调用 `vector_index.clear()` 后重建
- 单个 chunk 的 embedding 生成失败时，MUST 跳过该文件剩余 chunks（`break`），记录 warning 日志
- `vector_index` / `chunker` / `embed_fn` 任一为 None 时，跳过向量索引步骤，不影响 BM25/wikilink 索引

#### Scenario: Incremental embedding on file update

- **WHEN** `index_file()` 被调用且文件已有 3 个旧 chunks 在 VectorIndex 中
- **THEN** 先删除该 path 的 3 个旧 chunks，再重新分块并写入新 chunks

#### Scenario: Embedding API failure during indexing

- **WHEN** `embed_fn(chunk_text)` 抛出异常
- **THEN** 记录 warning 日志，跳过该文件剩余 chunks，BM25/wikilink 索引不受影响

#### Scenario: No embed_fn configured

- **WHEN** `AutoIndex` 的 `embed_fn` 为 None
- **THEN** `index_file()` 跳过向量索引步骤，仅执行 BM25/wikilink 索引

### Requirement: Embedding Function Injection

`MemoryService` SHALL 支持注入 embedding 函数，并传递给 `HybridSearch` 和 `AutoIndex`。

- `MemoryService.set_embed_fn(fn)` MUST 将 `fn` 同时注入 `HybridSearch` 和 `AutoIndex`
- `fn` 签名为 `def embed(text: str) -> list[float]`（同步函数）
- `main.py` 启动时 MUST 复用 RAG 子系统的 `_make_embed_fn(settings)` 产出 embedding 函数
- `EMBEDDING_API_KEY` 未配置时，`_make_embed_fn` 返回 None，记忆系统自动降级

#### Scenario: Embedding function available at startup

- **WHEN** `EMBEDDING_API_KEY` 已配置且 `main.py` 启动
- **THEN** `_make_embed_fn(settings)` 返回有效函数，注入 `MemoryService`，`HybridSearch` 启用两路 RRF 搜索，`AutoIndex` 启用向量索引

#### Scenario: Embedding function not available at startup

- **WHEN** `EMBEDDING_API_KEY` 未配置
- **THEN** `_make_embed_fn(settings)` 返回 None，记忆系统正常启动，搜索和索引均退化为无向量模式

#### Scenario: Embedding function injected after initialization

- **WHEN** `set_embed_fn(fn)` 在 `initialize()` 之后被调用
- **THEN** `HybridSearch` 和 `AutoIndex` 立即获得 embed_fn，下次搜索/索引时生效，无需重新初始化
