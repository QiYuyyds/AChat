# KGStore 知识图谱迁移计划

> 将 AGI-memory 的知识图谱模块（Extractor + KGStore）迁移到 AChat，补全 RAG 三路检索中的 Neo4j KG 路径。

## 1. 背景与目标

### 1.1 现状

AChat 的 RAG 混合检索架构设计了三路融合：

```
rag_search → HybridStore.search()
               ├── Milvus  语义向量搜索  ✅ 已 wire
               ├── ES      BM25 关键词   ✅ 已 wire
               └── Neo4j   知识图谱遍历  ❌ 未 wire（set_kg_backend 从未被调用）
```

`hybrid.py` 中 `_kg_ok()` 永远返回 `False`，KG 搜索路径被跳过。Neo4j 中只有 `GraphMemory` 写入的 `Memory` 节点（对话记忆），没有从文档中抽取的 `Entity` 节点。

### 1.2 目标

```
文档上传 → RAGEngine.ingest()
  ├── Milvus  向量入库         ✅ 已有
  ├── ES      全文索引         ✅ 已有
  └── KGStore.index_document() ← 本次迁移
        └── Extractor.extract(content)
              ├── Entity 节点 → Neo4j (:Entity {name, type, doc_hash, pg_id})
              └── Relation 边 → Neo4j (a)-[:RELATES_TO]->(b)

rag_search → HybridStore._fetch_kg()
               └── KGStore.search(query, k)
                     └── Extractor.extract(query) → 实体匹配 → 1~2跳遍历 → 返回 pg_id 列表
```

## 2. 源项目分析

### 2.1 源文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `internal/graph/types.py` | 63 | 数据结构：Entity, Relation, GraphSearchResult, ExtractResult, ChunkRef |
| `internal/graph/extractor.py` | 144 | LLM 实体关系抽取器（含 system prompt + JSON 解析 + 清洗） |
| `internal/graph/kgstore.py` | 287 | Neo4j 存储层：index_document / delete_document / search |
| `internal/graph/__init__.py` | 40 | 模块导出 |

### 2.2 核心接口

```python
# Extractor — 通过注入的 llm_fn 回调抽取实体和关系
class Extractor:
    def __init__(self, llm_fn: Optional[LLMFn])  # llm_fn(system_prompt, user_msg) -> str
    def extract(self, text: str) -> ExtractResult  # 返回 entities + relations

# KGStore — Neo4j 知识图谱存储与检索
class KGStore:
    def __init__(self, cfg, neo4j_client, llm_fn)
    def index_document(self, doc_hash: str, chunks: List[ChunkRef]) -> None  # 文档摄入
    def delete_document(self, doc_hash: str) -> None                         # 文档删除
    def search(self, query_text: str, top_k: int) -> List[GraphSearchResult] # 图检索
```

### 2.3 数据模型

```cypher
// Entity 节点
(:Entity {name, type, doc_hash, chunk_id, pg_id})

// Relation 边（动态关系类型）
(:Entity {name:"Harness"})-[:PART_OF {doc_hash, chunk_id, pg_id}]->(:Entity {name:"AChat"})
```

实体类型：Person, Organization, Location, Concept, Event, Product, Unknown
关系类型：RELATES_TO, PART_OF, CAUSES, DESCRIBES, MENTIONS, WORKS_FOR, LOCATED_IN

### 2.4 搜索流程

1. `Extractor.extract(query_text)` → 从查询中抽取实体名
2. Cypher `MATCH (e:Entity) WHERE e.name IN $names` → 种子节点
3. `apoc.path.subgraphNodes` 1~2 跳遍历 → 关联节点
4. 返回 `GraphSearchResult {pg_id, score, entities, hop_path}`
5. APOC 不可用时降级为直接节点匹配（`_search_direct`）

## 3. 目标项目现状

### 3.1 已有接口（无需改动）

| 文件 | 接口 | 说明 |
|------|------|------|
| `hybrid.py` L84-87 | `set_kg_backend(search_fn)` | 注入 KG 搜索回调 |
| `hybrid.py` L98-101 | `_kg_ok()` | 检查 KG 是否可用 |
| `hybrid.py` L379-387 | `_fetch_kg(query, fetch_k)` | KG 搜索路径（async） |
| `hybrid.py` L269-274 | RRF 融合中 KG 分支 | 已有权重计算逻辑 |
| `main.py` L83-88 | `generate_fn` 注入 | 已有 LLM 回调可用于 Extractor |

### 3.2 需要新增/修改的接口

| 文件 | 改动 | 说明 |
|------|------|------|
| `app/graph/__init__.py` | 新建 | 模块导出 |
| `app/graph/types.py` | 新建 | 从 AGI-memory 移植（几乎原样） |
| `app/graph/extractor.py` | 新建 | 从 AGI-memory 移植（几乎原样） |
| `app/graph/kgstore.py` | 新建 | 从 AGI-memory 移植 + async 适配 |
| `hybrid.py` L113-171 | 修改 `index_chunks` | 新增 KG 入库调用 |
| `main.py` | 新增 `_wire_kg_to_rag` | KG 搜索/入库/删除回调注入 |

## 4. 迁移方案

### 4.1 目录结构

```
backend/app/graph/
├── __init__.py     # 模块导出
├── types.py        # Entity, Relation, GraphSearchResult, ExtractResult, ChunkRef
├── extractor.py    # Extractor 类（LLM 实体关系抽取）
└── kgstore.py      # KGStore 类（Neo4j 存储与检索，async 适配）
```

### 4.2 适配点详解

#### 适配 1：Neo4j 驱动从同步改为 async

**AGI-memory（同步）**：
```python
# kgstore.py
self.neo4j.run_cypher(query, params)  # 同步调用，返回 list[dict]
```

**AChat（async）**：
```python
# kgstore.py — 适配后
async def _run_cypher(self, query: str, params: dict) -> list:
    async with self._driver.session() as session:
        result = await session.run(query, parameters=params)
        return await result.data()
```

GraphMemory 已经使用了 `AsyncDriver`，KGStore 共用同一个 driver 实例。

#### 适配 2：index_document 从同步改为 async

**AGI-memory（同步）**：
```python
def index_document(self, doc_hash: str, chunks: List[ChunkRef]) -> None:
    for c in chunks:
        result = self.extractor.extract(c.content)
        ...
```

**AChat（async）**：
```python
async def index_document(self, doc_hash: str, chunks: List[ChunkRef]) -> None:
    for c in chunks:
        result = self.extractor.extract(c.content)  # LLM 调用仍是同步
        await self._upsert_entity(ent)
        await self._upsert_relation(rel)
```

在 `hybrid.py` 的 `index_chunks` 中以 fire-and-forget 方式调用：
```python
# hybrid.py index_chunks 末尾
if self._kg_index_fn and self._kg_ok():
    chunk_refs = [ChunkRef(id=i, pg_id=pid, content=contents[i]) for i, pid in enumerate(pg_ids)]
    asyncio.create_task(self._kg_index_fn(doc_hash, chunk_refs))
```

#### 适配 3：search 返回格式对齐

**AGI-memory**：返回 `List[GraphSearchResult]`（dataclass 对象）

**AChat hybrid.py 期望**：`List[dict]`（含 `pg_id` 键）

```python
# kgstore.py — 适配后
async def search(self, query_text: str, top_k: int) -> List[dict]:
    results = await self._search_impl(query_text, top_k)
    return [
        {"pg_id": r.pg_id, "content": "", "score": r.score, "entities": r.entities}
        for r in results
    ]
```

#### 适配 4：_fetch_kg 中调用 async search

**当前 hybrid.py L383**：
```python
hits = self._kg_search_fn(query, fetch_k) or []  # 同步调用
```

**适配后**：
```python
hits = (await self._kg_search_fn(query, fetch_k)) or []  # async 调用
```

与 ES search 的 await 修复模式一致。

#### 适配 5：LLM 回调注入

AGI-memory 的 Extractor 接受 `llm_fn: Callable[[str, str], str]`（同步签名）。
AChat 的 `_make_generate_fn` 已生成同步 `generate(system_prompt, user_msg) -> str`，可直接注入。

```python
# main.py _wire_kg_to_rag 中
generate_fn = _make_generate_fn(settings)  # 已有
extractor = Extractor(generate_fn)         # 直接传入
```

### 4.3 main.py 新增 wiring 函数

```python
def _wire_kg_to_rag(rag_service, neo4j_driver, settings, generate_fn):
    """Wire KGStore into RAGService's HybridStore."""
    from app.graph.kgstore import KGStore
    from app.graph.extractor import Extractor

    extractor = Extractor(generate_fn)
    kg_store = KGStore(settings, neo4j_driver, extractor)

    async def kg_search(query_text, k):
        return await kg_store.search(query_text, k)

    async def kg_index(doc_hash, chunks):
        await kg_store.index_document(doc_hash, chunks)

    async def kg_delete(doc_hash):
        await kg_store.delete_document(doc_hash)

    rag_service.set_kg_backend(kg_search)
    rag_service.set_kg_index_fn(kg_index)       # 新增接口
    rag_service.set_kg_delete_fn(kg_delete)     # 新增接口
    logger.info("RAG: KG backend wired")
```

### 4.4 hybrid.py 新增接口

```python
# __init__ 中新增
self._kg_index_fn: Optional[Callable] = None    # (doc_hash, chunks) -> None
self._kg_delete_fn: Optional[Callable] = None   # (doc_hash) -> None

# 新增 setter
def set_kg_index_fn(self, fn: Callable) -> None:
    self._kg_index_fn = fn

def set_kg_delete_fn(self, fn: Callable) -> None:
    self._kg_delete_fn = fn

# _kg_ok 更新 — 同时检查 search_fn 和 driver 可用性
def _kg_ok(self) -> bool:
    return self._kg_search_fn is not None

# index_chunks 末尾新增 KG 入库
if self._kg_index_fn and self._kg_ok():
    chunk_refs = [ChunkRef(id=i, pg_id=pid, content=contents[i]) for i, pid in enumerate(pg_ids)]
    asyncio.create_task(self._kg_index_fn(doc_hash, chunk_refs))

# _fetch_kg 中添加 await
hits = (await self._kg_search_fn(query, fetch_k)) or []
```

### 4.5 RAGService 透传接口

```python
# rag_service.py 新增
def set_kg_backend(self, search_fn: Callable) -> None:
    self._hybrid.set_kg_backend(search_fn)

def set_kg_index_fn(self, fn: Callable) -> None:
    self._hybrid.set_kg_index_fn(fn)

def set_kg_delete_fn(self, fn: Callable) -> None:
    self._hybrid.set_kg_delete_fn(fn)
```

## 5. 实施步骤

### Task 1: 创建 graph 模块

- [ ] 创建 `backend/app/graph/__init__.py`
- [ ] 创建 `backend/app/graph/types.py`（从 AGI-memory 移植，原样）
- [ ] 创建 `backend/app/graph/extractor.py`（从 AGI-memory 移植，原样）
- [ ] 创建 `backend/app/graph/kgstore.py`（从 AGI-memory 移植 + async 适配）

### Task 2: 修改 hybrid.py

- [ ] `__init__` 新增 `_kg_index_fn` / `_kg_delete_fn` 字段
- [ ] 新增 `set_kg_index_fn` / `set_kg_delete_fn` setter
- [ ] `index_chunks` 末尾新增 KG 入库调用（fire-and-forget）
- [ ] `_fetch_kg` 中 `self._kg_search_fn()` 添加 `await`
- [ ] 新增 `delete_by_doc_hash` 中调用 `_kg_delete_fn`

### Task 3: 修改 RAGService 透传

- [ ] `rag_service.py` 新增 `set_kg_index_fn` / `set_kg_delete_fn` 透传方法

### Task 4: 修改 main.py wiring

- [ ] 新增 `_wire_kg_to_rag` 函数
- [ ] 在 `startup` 中调用 `_wire_kg_to_rag`（需 Neo4j driver + generate_fn）
- [ ] 日志输出 `RAG: KG backend wired`

### Task 5: 端到端验证

- [ ] 上传文档 → 检查 Neo4j 中 `:Entity` 节点和关系
- [ ] `rag_search` → 检查日志中出现 KG 搜索路径
- [ ] 删除文档 → 检查 Neo4j 中 Entity 和关系被清理
- [ ] Neo4j 不可用时 → 检查降级（KG 路径跳过，Milvus+ES 正常）

## 6. 风险与降级

### 6.1 APOC 插件

KGStore.search 使用 `apoc.path.subgraphNodes` 做多跳遍历。如果 Neo4j 未安装 APOC 插件，自动降级为 `_search_direct`（直接实体匹配，无遍历）。

**检查方式**：在 Neo4j Browser 中执行 `RETURN apoc.version()`，如果返回版本号则 APOC 可用。

### 6.2 LLM 不可用

Extractor 依赖 `generate_fn`（LLM 回调）。如果 `DEEPSEEK_API_KEY` 为空：
- Extractor 返回空结果（`ExtractResult()`）
- `index_document` 跳过所有 chunks（无实体抽取）
- `search` 返回空列表（无查询实体）
- KG 路径静默降级，不影响 Milvus + ES 两路检索

### 6.3 Neo4j 不可用

KGStore 所有方法在 Neo4j 不可用时返回空结果/空操作，不抛异常。与 GraphMemory 的降级策略一致。

### 6.4 性能影响

- `index_document` 以 fire-and-forget 方式运行（`asyncio.create_task`），不阻塞 `index_chunks` 主流程
- 每个 chunk 调用一次 LLM 抽取实体，41 个 chunks = 41 次 LLM 调用（后台异步执行）
- `search` 每次查询调用一次 LLM 抽取查询实体 + 一次 Cypher 遍历

## 7. 与 GraphMemory 的关系

| 维度 | GraphMemory | KGStore |
|------|-------------|---------|
| 节点标签 | `:Memory` | `:Entity` |
| 节点属性 | mem_id, content, importance | name, type, doc_hash, pg_id |
| 边类型 | FOLLOWS, SIMILAR_TO, CAUSES | RELATES_TO, PART_OF, CAUSES, ... |
| 数据来源 | 对话记忆（MemoryService） | 文档内容（RAGEngine.ingest） |
| 搜索入口 | `find_related(mem_id)` | `search(query_text, k)` |
| Neo4j Driver | AsyncDriver（独占） | AsyncDriver（共享同一实例） |
| 代码位置 | `app/memory/graph_memory.py` | `app/graph/kgstore.py`（新建） |

两者共用同一个 Neo4j `AsyncDriver` 实例，但操作不同的节点标签和边类型，互不干扰。

## 8. 验证方案

### 8.1 入库验证

上传文档后，在 Neo4j Browser 中执行：
```cypher
// 检查 Entity 节点
MATCH (e:Entity) RETURN e.name, e.type, e.doc_hash, e.pg_id LIMIT 10

// 检查 Relation 边
MATCH (a:Entity)-[r]->(b:Entity) RETURN a.name, type(r), b.name LIMIT 10

// 统计
MATCH (e:Entity) RETURN count(e) AS entities
MATCH ()-[r]->() WHERE r.doc_hash IS NOT NULL RETURN count(r) AS relations
```

### 8.2 检索验证

后端日志应出现：
```
RAG: KG backend wired                    ← 启动时
KG search: 3 entities extracted          ← 搜索时
```

hybrid.py 的 `_fetch_kg` 返回非空 `_PathHits(ok=True)`，RRF 融合中 KG 分支有贡献。

### 8.3 删除验证

删除文档后，Neo4j 中对应 `doc_hash` 的 Entity 和 Relation 被清理：
```cypher
MATCH (e:Entity {doc_hash: "xxx"}) RETURN count(e)  // 应为 0
```
