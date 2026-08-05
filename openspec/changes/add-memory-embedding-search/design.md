# Design — add-memory-embedding-search

## Context

AChat 的文件原生记忆系统（`backend/app/memory/`）当前检索路径为：

```
HybridSearch.search(query)
  → BM25Index.search(query)          # SQLite FTS5 关键词匹配
  → WikilinkExpander.expand(seeds)   # 1-hop 图扩展 → 参与 RRF 排序
  → RRF fusion(bm25_weight=0.7, wl_weight=0.3, k=60)
  → _build_expansion(path)           # wikilink 邻居元数据附加（后处理）
```

问题在于 wikilink 同时承担了两个职责：**RRF 排序参与者**和**结果扩展元数据来源**。作为排序参与者，wikilink 1-hop 扩展会引入大量弱关联文件（"A 提到了 B"不代表 B 与 query 相关），这些噪声文件稀释了真正相关结果的排名。

ReMe 项目的 `SearchStep` 架构提供了更清晰的关注点分离：
- BM25 + Vector 两路 RRF 回答"哪些 chunk 与 query 相关"
- wikilink 后处理扩展回答"这些相关的 chunk 还关联了什么"

ReMe 的 `search.py` 第 251-254 行清晰展示了这一分层：
```python
# RRF 融合完成后，才做 wikilink 扩展
unique_paths = list(dict.fromkeys(c.path for c in fused))
link_expansion = await expand_links(self.file_store, unique_paths, ...)
```

AChat 的约束：
1. **不引入 FAISS**——记忆文件量级 <10k chunks，暴力搜索足够
2. **向量存 SQLite**——与 BM25 的 `bm25.db`、wikilink 的 `wikilinks.db` 对称，保持 file-native 架构
3. **复用 RAG 的 embedding key**——`EMBEDDING_API_KEY` / `embedding_api_url` / `embedding_model` 已在 `config.py` 中定义，`main.py` 的 `_make_embed_fn()` 已实现 httpx 调用 OpenAI-compatible embeddings API

## Goals / Non-Goals

**Goals:**

- 在记忆系统中新增向量检索能力，与 BM25 做两路 RRF 融合（学习 ReMe 架构）
- 将 wikilink 从 RRF 排序参与者移除，降为后处理扩展（仅附加邻居元数据，不参与排序）
- 学习 ReMe 的 AST 分块策略，适配 AChat 的 Markdown frontmatter + body 结构
- 增量索引：文件变更时自动生成/更新 embedding，与 `auto_index` 流程一致
- 优雅降级：`EMBEDDING_API_KEY` 未配置时退化为纯 BM25 + wikilink 后处理

**Non-Goals:**

- 不引入 FAISS 或其他 ANN 库（暴力搜索在当前量级足够）
- 不修改 RAG 子系统的 Milvus/ES 基础设施
- 不修改前端 UI（记忆检索是后端内部能力，前端通过 `recall()` 透明使用）
- 不做 embedding 模型微调或本地模型推理
- 不做向量量化压缩（float32 精度保留，后续量级增长时再考虑）
- 不删除 `WikilinkExpander`——它仍用于 `auto_index` 图维护和后处理扩展

## Decisions

### D1. 向量存储：SQLite BLOB + 内存全量加载

```python
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_vectors (
    path TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    agent_id TEXT DEFAULT '',
    bucket TEXT DEFAULT '',
    PRIMARY KEY (path, chunk_idx)
);
CREATE INDEX IF NOT EXISTS idx_mv_agent ON memory_vectors(agent_id);
CREATE INDEX IF NOT EXISTS idx_mv_bucket ON memory_vectors(bucket);
"""
```

向量序列化：`struct.pack(f'{dim}f', *vec)` → BLOB；反序列化：`struct.unpack(f'{dim}f', blob)`。

搜索时一次性 `SELECT path, chunk_text, embedding FROM memory_vectors` 全量加载到内存，逐条计算余弦相似度。

**选择**：SQLite BLOB 而非 JSON / numpy `.npy` 文件。
**理由**：
- 与 `BM25Index`、`WikilinkExpander` 的 SQLite 存储对称，统一管理
- BLOB 比 JSON 紧凑 4x（float32 vs JSON 数字字符串）
- 单文件 DB 便于原子性更新和清理
- 不引入 numpy 依赖（纯 `struct` + list comprehension 足够）

**替代方案**：
- FAISS：性能更好但引入 C++ 依赖，当前量级不需要
- numpy `.npy`：需要单独管理文件生命周期，不如 SQLite 事务安全
- 内存 dict：进程重启丢失，不支持跨会话持久化

### D2. Markdown 分块策略：标题层级 + breadcrumb + frontmatter 注入

```python
@dataclass
class Chunk:
    text: str           # 完整 chunk 文本（含 breadcrumb 前缀）
    section_path: str   # "项目配置 > 前端框架 > React 19"
    char_count: int

class MarkdownChunker:
    def chunk(self, mem_file: MemoryFile) -> list[Chunk]:
        # 1. 提取 frontmatter name/description/tags 作为全局前缀
        prefix = f"{mem_file.frontmatter.name}\n{mem_file.frontmatter.description}"

        # 2. 按 ## / ### 标题分块（# 视为文件标题，不单独成块）
        sections = self._split_by_headings(mem_file.body)

        # 3. 每个 section 拼接 breadcrumb：上级标题 > 当前标题
        # 4. 短段合并：< min_chunk_size 的相邻段合并
        # 5. 超长段二次切分：按 chunk_size 字符数切分（不截断句子）
        # 6. 每个 chunk 最终文本 = prefix + "\n" + breadcrumb + "\n" + section_body
```

**选择**：标题层级分块而非固定 token 窗口。
**理由**：
- 记忆文件是结构化 Markdown（daily card / digest），标题天然是语义边界
- ReMe 的 `MarkdownFileChunker` 已验证此策略的效果
- frontmatter 注入增强语义信号：name 通常是高度概括的检索锚点

**参数**：
- `chunk_size = 512`（字符数，近似 token 估算；中文 1 字符 ≈ 1-2 token，英文 1 word ≈ 1-1.5 token）
- `min_chunk_size = 100`（低于此长度的相邻 chunk 合并）

**与 ReMe 的差异**：
- ReMe 用 `mistune` 做 AST 解析，AChat 用正则 `^#{1,6}\s` 分割——更轻量，不引入 mistune 依赖
- ReMe 的 breadcrumb 包含完整路径，AChat 简化为 `>` 分隔的标题链——足够语义化
- AChat 额外注入 frontmatter prefix——ReMe 不做这个

### D3. 两路 RRF 融合：BM25 + Vector（wikilink 移至后处理）

学习 ReMe 的 `SearchStep` 架构，搜索流程重构为三阶段：

```
Phase 1: 两路并行检索
  BM25Index.search(query)     → bm25_ranked: {path: rank}
  VectorIndex.search(emb)     → vector_ranked: {path: rank}  (文件级聚合后)

Phase 2: 两路 RRF 融合（wikilink 不参与）
  all_paths = bm25_ranked.keys() | vector_ranked.keys()
  for path in all_paths:
    score = bm25_weight/(k+bm25_rank) + vector_weight/(k+vector_rank)
  → top_k paths

Phase 3: wikilink 后处理扩展（不参与排序）
  for path in top_k_paths:
    expansion = _build_expansion(path)  # 已有方法，附加 outlinks/inlinks 元数据
```

```python
# 权重配置（与 ReMe 默认值对齐）
bm25_weight = 0.3    # 从 0.7 降低
vector_weight = 0.7  # 向量主导（与 ReMe 的 vector_weight=0.7 一致）
# wikilink_weight 废弃——不再参与 RRF
```

**向量搜索 → 文件级聚合**：
- Vector search 返回 `(path, chunk_idx, score)` 列表
- 同一 path 的多个 chunk 命中时，取最高分 chunk 作为该 path 的代表
- 映射为 `path → rank`，与 BM25 的 rank 统一做 RRF

**降级逻辑**：
```python
if self.embed_fn and self.vector_index and self.vector_index.count() > 0:
    # BM25 + Vector 两路 RRF
else:
    # 纯 BM25 搜索（wikilink 后处理仍执行）
```

**选择**：vector_weight = 0.7（向量主导），与 ReMe 默认值对齐。
**理由**：
- ReMe 的评测数据已验证 vector_weight=0.7 优于均权配置
- 向量搜索的语义召回能力是本次变更的核心目标，应给予足够权重
- BM25 作为精确匹配的补充，0.3 权重足以让关键词精确命中的结果获得合理排名
- 权重可配置，后续可根据 AChat 自己的评测调优

**wikilink 移除出 RRF 的影响**：
- 当前 wikilink 在 RRF 中的权重为 0.3，移除后这些"图关联但非关键词/非语义匹配"的文件不再自动进入排序结果
- 但通过 `_build_expansion()` 后处理，wikilink 邻居的 name/description 仍作为元数据附加在结果上，LLM 可见
- 这避免了图扩展噪声污染排序，同时保留了图关联上下文的可用性

### D4. Embedding 注入链路

```
main.py startup
  → _make_embed_fn(settings)          # 复用 RAG 的 embedding 函数
  → _rag_service.set_embed_fn(fn)     # RAG 子系统注入（已有）
  → _memory_service.set_embed_fn(fn)  # 记忆子系统注入（新增）
      → HybridSearch.embed_fn = fn
      → AutoIndex.embed_fn = fn
```

`embed_fn` 签名：`def embed(text: str) -> list[float]`（同步函数，内部 httpx 调用）。

在 `auto_index.index_file()` 中调用 embedding 时使用 `asyncio.to_thread(embed, chunk_text)` 包装，避免阻塞事件循环。

**选择**：同步 `embed_fn` + `to_thread` 包装，而非改为 async。
**理由**：
- `_make_embed_fn` 已定义为同步函数（httpx.Client），RAG 子系统也在用
- 改为 async 需要改 RAG 子系统的接口，超出本变更范围
- `to_thread` 的开销可接受（embedding API 调用本身是 I/O bound）

### D5. auto_index 集成：增量 embedding

```python
class AutoIndex:
    def __init__(self, ..., vector_index=None, chunker=None, embed_fn=None):
        self.vector_index = vector_index
        self.chunker = chunker
        self._embed_fn = embed_fn

    def index_file(self, filepath: Path) -> None:
        # ... 现有 BM25 + wikilink 逻辑 ...

        # Vector index（新增）
        if self.vector_index and self.chunker and self._embed_fn:
            rel = self._rel_path(filepath)
            self.vector_index.remove(rel)  # 先删旧
            chunks = self.chunker.chunk(mem_file)
            for idx, chunk in enumerate(chunks):
                try:
                    emb = self._embed_fn(chunk.text)  # 同步调用
                    self.vector_index.add(rel, idx, chunk.text, emb,
                                          agent_id=mem_file.frontmatter.agent_id,
                                          bucket=bucket)
                except Exception as e:
                    logger.warning("Embedding failed for %s chunk %d: %s", rel, idx, e)
                    break  # 跳过该文件剩余 chunks，但不阻断索引流程
```

**选择**：embedding 失败时 `break` 而非 `continue`。
**理由**：embedding API 通常是整体不可用（key 错误/网络问题），`continue` 会产生大量重复错误日志。`break` 跳过该文件剩余 chunks，但下一个文件会重新尝试。

### D6. 配置项

```python
# config.py 变更
memory_bm25_weight: float = 0.3       # 从 0.7 调整
memory_vector_weight: float = 0.7     # 新增
# memory_wikilink_weight: float = 0.3  # 废弃——保留字段不删，但 HybridSearch 不再读取
memory_chunk_size: int = 512
memory_chunk_min_size: int = 100
```

**选择**：`memory_wikilink_weight` 保留字段但不读取，而非删除。
**理由**：已有 `.env` 中可能配置了此值，删除字段会导致 pydantic-settings 报错。保留为 no-op 字段，在注释中标记废弃。

## Risks / Trade-offs

- **[wikilink 排序信号丢失]** → wikilink 从 RRF 移除后，纯图关联（无关键词/语义匹配）的文件不再出现在搜索结果排序中。→ 缓解：通过 `_build_expansion()` 后处理，wikilink 邻居元数据仍附加在结果上供 LLM 参考。ReMe 的评测验证了此架构的召回率优于三路融合。
- **[暴力搜索延迟]** → 当前记忆文件量级 <10k chunks，全量余弦相似度计算 <50ms。后续量级增长到 >50k chunks 时考虑引入 FAISS 或向量量化。
- **[embedding API 调用量]** → 每次 `index_file` 对每个 chunk 调用一次 embedding API。一个 daily card 通常分 2-5 个 chunks，成本可控。`full_reindex` 时会批量调用，可考虑后续加 batch embedding 优化。
- **[embedding 维度不一致]** → 用户切换 embedding model 后维度变化，旧向量无法与新查询匹配。→ 迁移策略：`full_reindex()` 时检测维度变化，自动清空重建。
- **[EMBEDDING_API_KEY 未配置]** → `VectorIndex` 正常初始化但无数据，`HybridSearch` 自动退化为纯 BM25 + wikilink 后处理，零功能损失。
- **[同步 embed_fn 阻塞事件循环]** → `auto_index.index_file()` 是同步方法，在 `_safe_auto_memory` 中通过 `asyncio.create_task` 调用时已在事件循环中。embedding 调用使用 `asyncio.to_thread` 包装避免阻塞。
