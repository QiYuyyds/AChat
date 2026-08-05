# Tasks: add-memory-embedding-search

## 1. VectorIndex — SQLite BLOB 向量存储

- [x] 1.1 创建 `backend/app/memory/search/vector_index.py`，定义 `VectorIndex` 类，包含 `__init__(db_path)` / `initialize()` / `close()` 方法
- [x] 1.2 实现 `_CREATE_TABLE_SQL`：创建 `memory_vectors` 表（path, chunk_idx, chunk_text, embedding BLOB, agent_id, bucket）+ `idx_mv_agent` / `idx_mv_bucket` 索引
- [x] 1.3 实现 `add(path, chunk_idx, chunk_text, embedding, agent_id, bucket)` 方法：`struct.pack` 序列化向量为 BLOB，INSERT OR REPLACE，校验维度一致性
- [x] 1.4 实现 `remove(path)` 方法：删除指定 path 的所有 chunks
- [x] 1.5 实现 `search(query_embedding, top_k, agent_id, bucket)` 方法：全量加载向量到内存，逐条计算余弦相似度，返回 `list[tuple[str, int, float]]`，按 score 降序
- [x] 1.6 实现 `clear()` 方法：清空 `memory_vectors` 表
- [x] 1.7 实现 `count()` 方法：返回向量总数
- [x] 1.8 维度动态适配：首次 `add()` 时记录维度到实例变量 `_dim`，后续 `add()` 校验维度匹配，`search()` 校验 query 维度匹配

## 2. MarkdownChunker — AST 级语义分块

- [x] 2.1 创建 `backend/app/memory/search/chunker.py`，定义 `Chunk` dataclass（text, section_path, char_count）和 `MarkdownChunker` 类
- [x] 2.2 实现 `_split_by_headings(body)` 方法：按 `^#{2,3}\s` 正则分割 body 为 sections，每个 section 记录标题和正文
- [x] 2.3 实现 breadcrumb 生成：维护标题栈，`##` 入栈、`###` 入栈，section_path 用 ` > ` 连接标题链
- [x] 2.4 实现 frontmatter prefix 拼接：`name + "\n" + description` 作为全局前缀注入每个 chunk
- [x] 2.5 实现短段合并：相邻 section 文本长度 < `min_chunk_size` 时合并为一个 chunk
- [x] 2.6 实现超长段二次切分：section 文本长度 > `chunk_size` 时按段落边界（`\n\n`）切分，不满 `chunk_size` 的相邻段落合并
- [x] 2.7 实现 `chunk(mem_file)` 主方法：组装 prefix + breadcrumb + section_body → `Chunk` 列表
- [x] 2.8 处理无标题 fallback：body 不含 `##`/`###` 时返回单个 chunk（section_path=""）

## 3. HybridSearch — 重构为 BM25+Vector 两路 RRF + wikilink 后处理

- [x] 3.1 在 `HybridSearch.__init__` 新增可选参数 `vector_index: VectorIndex | None = None`、`embed_fn: Callable | None = None`
- [x] 3.2 重构 `search()` Phase 1：BM25 搜索 + Vector 搜索（当 embed_fn/vector_index 可用时）并行执行；移除 wikilink BFS 扩展参与排序的逻辑（删除当前 Phase 2 的 `expander.expand()` + `wl_ranked` 构建）
- [x] 3.3 实现向量搜索 + 文件级聚合：对 query 做 embedding → `vector_index.search()` 获取 top-k chunks → 同一 path 取最高分 chunk → 生成 `vector_ranked: dict[str, int]`
- [x] 3.4 重构 `search()` Phase 2：仅对 BM25 + Vector 两路做 RRF 融合，`all_paths = bm25_ranked.keys() | vector_ranked.keys()`，每个 path 的 score = `bm25_weight/(k+bm25_rank) + vector_weight/(k+vector_rank)`
- [x] 3.5 更新 `SearchResult.scores` dict：包含 `"bm25"` / `"vector"` / `"rrf"` 三个 key（移除 `"wikilink"` key）
- [x] 3.6 更新 `SearchResult.source`：可选值改为 `bm25` / `vector` / `rrf`（移除 `wikilink`）
- [x] 3.7 保留 `search()` Phase 3 wikilink 后处理：`_build_expansion(path)` 方法不变，继续为 top-k 结果附加 outlinks/inlinks 邻居元数据
- [x] 3.8 实现降级逻辑：`embed_fn` 为 None / `vector_index` 为 None / `count() == 0` / embedding 生成异常时，退化为纯 BM25 搜索 + wikilink 后处理（记录 warning 日志）
- [x] 3.9 更新权重读取：`bm25_weight` 从 `settings.memory_bm25_weight`（新默认 0.3）读取，`vector_weight` 从 `settings.memory_vector_weight`（新默认 0.7）读取；不再读取 `memory_wikilink_weight`
- [x] 3.10 更新 archived 状态降权逻辑：`scores["bm25"] *= 0.5` 后重算 `rrf = bm25 + vector`（不再加 wikilink 分量）
- [x] 3.11 更新模块 docstring：`Hybrid search — RRF fusion of BM25 + Vector, with wikilink post-processing expansion.`

## 4. AutoIndex — Embedding 生成集成

- [x] 4.1 在 `AutoIndex.__init__` 新增可选参数 `vector_index: VectorIndex | None = None`、`chunker: MarkdownChunker | None = None`、`embed_fn: Callable | None = None`
- [x] 4.2 在 `index_file()` 的 BM25 + wikilink 索引完成后，新增向量索引步骤：先 `vector_index.remove(rel)`，再 `chunker.chunk(mem_file)` 分块
- [x] 4.3 对每个 chunk 调用 `embed_fn(chunk.text)` 生成向量，`vector_index.add(rel, idx, chunk.text, emb, agent_id, bucket)` 写入
- [x] 4.4 embedding 生成失败时 `break` 跳过该文件剩余 chunks，记录 warning 日志
- [x] 4.5 在 `remove_file()` 中新增 `vector_index.remove(rel)` 清除向量
- [x] 4.6 在 `full_reindex()` 中新增 `vector_index.clear()` 清空后重建
- [x] 4.7 `vector_index` / `chunker` / `embed_fn` 任一为 None 时跳过向量索引步骤

## 5. MemoryService — 初始化注入

- [x] 5.1 在 `MemoryService.__init__` 新增 `VectorIndex` 实例（`self.workspace.metadata_dir / "vectors.db"`）
- [x] 5.2 在 `MemoryService.__init__` 新增 `MarkdownChunker` 实例（使用 `settings.memory_chunk_size` / `settings.memory_chunk_min_size`）
- [x] 5.3 将 `vector_index` 和 `chunker` 传入 `AutoIndex` 构造函数
- [x] 5.4 将 `vector_index` 传入 `HybridSearch` 构造函数（在 `_build_search()` 中）
- [x] 5.5 新增 `MemoryService.set_embed_fn(fn)` 方法：将 `fn` 同时注入 `HybridSearch` 和 `AutoIndex`
- [x] 5.6 在 `initialize()` 中新增 `self.vector_index.initialize()`
- [x] 5.7 在 `close()` 中新增 `self.vector_index.close()`
- [x] 5.8 更新 `__init__.py` 导出 `VectorIndex` 和 `MarkdownChunker`

## 6. 配置与启动注入

- [x] 6.1 在 `config.py` 的 `Settings` 类中：`memory_bm25_weight` 默认值从 0.7 改为 0.3；新增 `memory_vector_weight: float = 0.7`；新增 `memory_chunk_size: int = 512`、`memory_chunk_min_size: int = 100`；`memory_wikilink_weight` 保留字段但注释标记废弃
- [x] 6.2 在 `HybridSearch` 中使用 `settings.memory_vector_weight` 读取 vector 权重，使用 `settings.memory_bm25_weight` 读取 bm25 权重
- [x] 6.3 在 `main.py` 启动流程中，将 `_make_embed_fn(settings)` 的返回值注入 `_memory_service.set_embed_fn(fn)`
- [x] 6.4 在 `main.py` 中增加日志：embedding 注入成功/失败时分别记录 info/warning
- [x] 6.5 在 `backend/.env.example` 的 Memory 配置区域更新：`MEMORY_BM25_WEIGHT` 默认值改为 0.3；新增 `MEMORY_VECTOR_WEIGHT=0.7`；`MEMORY_WIKILINK_WEIGHT` 标记废弃注释；新增 `MEMORY_CHUNK_SIZE` / `MEMORY_CHUNK_MIN_SIZE`

## 7. 测试

- [x] 7.1 新增 `VectorIndex` 单元测试：add → search → remove → search 返回空；维度不匹配拒绝写入；count() 正确
- [x] 7.2 新增 `MarkdownChunker` 单元测试：标题分块、breadcrumb 生成、短段合并、超长段切分、无标题 fallback、frontmatter prefix 注入
- [x] 7.3 新增 `HybridSearch` 集成测试：两路 RRF 融合（mock embed_fn + vector_index 有数据）、降级测试（embed_fn=None 退化为纯 BM25）、vector-only 命中场景、BM25-only 命中场景、wikilink 后处理 expansion 仍附加
- [x] 7.4 新增 `HybridSearch` 回归测试：wikilink 邻居不作为独立搜索结果出现（不在 top-k paths 中），但 expansion 字段包含邻居元数据
- [x] 7.5 新增 `AutoIndex` 集成测试：index_file 后 vector_index 有数据、remove_file 后 vector_index 清空、embedding 失败时 BM25 不受影响
- [x] 7.6 新增 `MemoryService` 集成测试：set_embed_fn 后 HybridSearch 启用两路搜索、EMBEDDING_API_KEY 未配置时降级
- [x] 7.7 运行 `ruff check .` 和 `pytest` 确保全部通过
