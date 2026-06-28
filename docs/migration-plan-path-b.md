# 路径 B（优化版）：全面拥抱 AGI-memory，无用户系统 — 迁移计划

> **版本**: v1.0  
> **生成日期**: 2026-06-25  
> **用途**: 作为新对话的完整上下文参考文档，指导逐步实施迁移  

---

## 0. 方案概要

### 0.1 核心决策

| 维度 | 决策 |
|------|------|
| 持久化 | SQLite → PostgreSQL（全量替换，9 张业务表 + 新增 6 张表） |
| 向量检索 | Milvus 2.4（替代 sqlite-vec） |
| 全文检索 | Elasticsearch 8 + IK 中文分词（替代 FTS5） |
| 知识图谱 | Neo4j 5（新增，记忆图增强 + RAG 图路检索） |
| 事件总线 | Kafka（可选新增，不替代现有 InProcess EventBus） |
| 用户系统 | **不做**。保持单用户设计 `user_id = "default_user"` |
| 前端 | **零改动**。后端 API 契约不变 |
| Electron | 可保留（作为本地服务集群壳，连 docker-compose） |

### 0.2 预期收益

```
SQLite JSON 文本列          → PG JSONB（二进制存储，查询快 10-100x）
手动 JSON 解析              → 原生 JSONB 操作符 (->>, @>, jsonb_array_elements)
无全文索引                  → ES BM25 + IK 中文分词（中文检索质量飞跃）
sqlite-vec 暴力搜索         → Milvus ANN（百万级文档向量检索）
无图能力                    → Neo4j 多跳遍历（记忆关联 + RAG 图路）
无记忆系统                  → 三层记忆 + 图增强 + consolidate
无 RAG 检索                 → 三路 RRF 融合 + LLM 改写/精排
硬编码 context              → Schema 驱动 Prompt 装配器 + token 预算裁剪
```

### 0.3 预期工作量

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 0 | PG 切换 + docker-compose | 3-5 天 |
| Phase 1 | 记忆系统搬运 | 4-5 天 |
| Phase 2 | RAG 系统搬运 | 4-5 天 |
| Phase 3 | Prompt 装配器 | 3-4 天 |
| Phase 4 | AgentRunner 集成 | 2-3 天 |
| Phase 5 | 测试 + 打磨 | 3-5 天 |
| **总计** | | **16-27 天 ≈ 3-5 周（单人全职）** |

---

## 1. 项目关键路径索引

### 1.1 AChat 现有核心文件（改动目标）

| 文件 | 行数 | 改动类型 | 说明 |
|------|------|---------|------|
| `backend/app/config.py` | 83 | **改** | 扩展配置字段（Milvus/ES/Neo4j/Kafka/RAG/Memory 全部配置） |
| `backend/app/db/engine.py` | 93 | **改** | aiosqlite → asyncpg，删除 SQLite PRAGMA 钩子 |
| `backend/app/db/models.py` | 526 | **改** | Text → JSONB 类型升级 + 新增 6 张 ORM 模型 |
| `backend/app/services/conversation_context.py` | 348 | **改** | 对接 PromptAssembler |
| `backend/app/services/agent_runner.py` | 1366 | **改** | 加记忆写入 hook + 对接 PromptAssembler |
| `backend/app/main.py` | ~100 | **改** | 启动时 Infrastructure 初始化 |
| `backend/app/tools/registry.py` | ~80 | **改** | 注册 rag_search/rag_ingest/memory_recall 工具 |

### 1.2 新增文件（从 AGI-memory 搬运 + 适配）

```
backend/app/infra/
├── __init__.py
├── factory.py          ← build_infrastructure() 配置驱动装配
├── status.py           ← InfrastructureStatus 状态聚合
└── hybrid.py           ← HybridStore 三路 RRF 融合检索

backend/app/memory/
├── __init__.py
├── short_term.py       ← ShortTerm (直接复用，纯内存 deque)
├── long_term.py        ← LongTerm (搬运 + async 改造)
├── preference.py       ← Preference (搬运 + async 改造)
├── graph_memory.py     ← GraphMemory (搬运 + async neo4j)
├── consolidation.py    ← ConsolidationConfig + consolidate 算法 (直接复用)
└── memory_service.py   ← MemoryService 装配入口

backend/app/rag/
├── __init__.py
├── splitter.py         ← RecursiveSplitter (直接复用)
├── rewriter.py         ← LLMRewriter (直接复用)
├── reranker.py         ← LLMReranker (直接复用)
├── rag_engine.py       ← RAG Engine 入口 (切分→入库→检索→合成)
└── rag_service.py      ← RAGService 装配入口

backend/app/services/
├── memory_service.py   ← 记忆服务（集成到 AgentRunner）
├── rag_service.py      ← RAG 服务（集成到 ToolRegistry）
└── prompt_assembler.py ← Prompt 上下文装配器

docker-compose.yml      ← 全套服务编排
```

### 1.3 AGI-memory 源文件映射表

| 源文件 (AGI-memory) | 目标文件 (AChat) | 搬运方式 |
|---|---|---|
| `internal/memory/memory.py` → `ShortTerm` 类 | `backend/app/memory/short_term.py` | A. 直接复用 |
| `internal/memory/memory.py` → `_tokenize_zh()` | `backend/app/memory/consolidation.py` | A. 直接复用 |
| `internal/memory/memory.py` → `LongTerm` 类 | `backend/app/memory/long_term.py` | B. sync→async |
| `internal/memory/memory.py` → `Item/RecallFilter/ConsolidationResult` | `backend/app/memory/consolidation.py` | A. 直接复用 |
| `internal/memory/preference.py` → `Preference` 类 | `backend/app/memory/preference.py` | B. sync→async |
| `internal/memory/graph_memory.py` → `GraphMemory` 类 | `backend/app/memory/graph_memory.py` | B. sync→async |
| `internal/memory/mem_stack.py` → `MemoryStack/ConsolidationConfig` | `backend/app/memory/consolidation.py` | A. 直接复用 |
| `internal/rag/splitter.py` → `RecursiveSplitter` | `backend/app/rag/splitter.py` | A. 直接复用 |
| `internal/rag/rewriter.py` → `LLMRewriter` | `backend/app/rag/rewriter.py` | A. 直接复用 |
| `internal/rag/reranker.py` → `LLMReranker` | `backend/app/rag/reranker.py` | A. 直接复用 |
| `internal/rag/hybrid.py` → `HybridStore` | `backend/app/infra/hybrid.py` | C. 深度适配 |
| `internal/rag/rag.py` → `Engine` | `backend/app/rag/rag_engine.py` | C. 深度适配 |
| `internal/promptctx/slot.py` | `backend/app/services/prompt_assembler.py` | A. 直接复用 |
| `internal/promptctx/schema.py` | `backend/app/services/prompt_assembler.py` | C. 深度适配 |
| `internal/promptctx/assembler.py` | `backend/app/services/prompt_assembler.py` | C. 深度适配 |
| `internal/promptctx/source.py` | `backend/app/services/prompt_assembler.py` | C. 深度适配 |
| `internal/infra/infra.py` → `Status/_connect_*` | `backend/app/infra/factory.py` | C. 重写 async 版 |
| `internal/agent/restore.py` | `backend/app/main.py` | 参考逻辑，不搬运 |
| `internal/agent/memory_writer.py` | `backend/app/services/agent_runner.py` | 参考逻辑，内联 |

**搬运分类说明：**
- **A. 直接复用**：纯算法/纯内存代码，零改动或仅改 import 路径
- **B. sync→async**：算法不变，DB 读写从 psycopg2 改为 asyncpg via SQLAlchemy async session
- **C. 深度适配**：架构差异大，需要重写接口层（如 threading → asyncio.gather、数据源切换）
- **不需要搬运**：`agent/agent.py`（AChat 有 agent_runner）、`agent/router.py`（用 Adapter 选择）、`handler/*`（FastAPI 路由）、`sandbox/*`（Workspace 沙箱）、`tools/*`（自有 tool_registry）、`platform/*`（SQLAlchemy 统一 DB 访问）

---

## 2. Phase 0：PG 切换 + docker-compose（3-5 天）

### 2.1 docker-compose.yml 创建

在项目根目录创建 `docker-compose.yml`：

```yaml
version: "3.9"

services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql+asyncpg://agenthub:agenthub@postgres:5432/agenthub
      MILVUS_HOST: milvus
      MILVUS_PORT: 19530
      ES_ADDRESSES: http://elasticsearch:9200
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: agenthub-neo4j
      ENABLE_GRAPH: "true"
      EMBEDDING_API_KEY: ${OPENAI_API_KEY}
    depends_on:
      postgres: { condition: service_healthy }
    restart: unless-stopped

  frontend:
    build: .
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000
    depends_on: [backend]

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: agenthub
      POSTGRES_USER: agenthub
      POSTGRES_PASSWORD: agenthub
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agenthub"]
      interval: 5s
      retries: 5

  milvus-etcd:
    image: quay.io/coreos/etcd:v3.5.16
    environment:
      ETCD_AUTO_COMPACTION_MODE: revision
      ETCD_AUTO_COMPACTION_RETENTION: "1000"
    volumes: ["etcddata:/etcd"]

  milvus-minio:
    image: minio/minio:latest
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    command: minio server /minio_data
    volumes: ["miniodata:/minio_data"]

  milvus:
    image: milvusdb/milvus:v2.4-latest
    command: ["milvus", "run", "standalone"]
    environment:
      ETCD_ENDPOINTS: milvus-etcd:2379
      MINIO_ADDRESS: milvus-minio:9000
    ports: ["19530:19530"]
    depends_on: [milvus-etcd, milvus-minio, postgres]
    volumes: ["milvusdata:/var/lib/milvus"]

  elasticsearch:
    image: elasticsearch:8.14.0
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - "ES_JAVA_OPTS=-Xms512m -Xmx512m"
    ports: ["9200:9200"]
    volumes: ["esdata:/usr/share/elasticsearch/data"]

  neo4j:
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: neo4j/agenthub-neo4j
      NEO4J_PLUGINS: '["apoc"]'
    ports: ["7474:7474", "7687:7687"]
    volumes: ["neo4jdata:/data"]

volumes:
  pgdata:
  etcddata:
  miniodata:
  milvusdata:
  esdata:
  neo4jdata:
```

### 2.2 config.py 扩展

**文件**: `backend/app/config.py`  
**当前**: 仅有 `database_url` (SQLite) + API Keys + CORS  
**目标**: 增加全部 AGI-memory 基础设施配置字段

```python
class Settings(BaseSettings):
    # ─── 现有（保留）───
    database_url: str = "postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    ark_api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    cors_origins: str = "http://localhost:3000"
    workspace_root: str = "../.agenthub-data/workspaces"
    data_dir: str = "../.agenthub-data"

    # ─── 新增：Milvus ───
    milvus_host: str = ""
    milvus_port: int = 19530

    # ─── 新增：Elasticsearch ───
    es_addresses: str = ""           # 逗号分隔

    # ─── 新增：Neo4j ───
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    enable_graph: bool = False
    kg_max_hops: int = 2
    kg_weight: float = 0.3

    # ─── 新增：Kafka（可选）───
    kafka_brokers: str = ""

    # ─── 新增：Embedding ───
    embedding_api_key: str | None = None
    embedding_api_url: str | None = None
    embedding_model: str | None = None

    # ─── 新增：RAG ───
    rag_chunk_size: int = 200
    rag_chunk_overlap: int = 50
    rag_top_k: int = 3
    rag_rrf_constant_k: int = 60
    rag_semantic_weight: float = 0.7
    rag_milvus_dim: int = 1024
    rag_rewrite_enabled: bool = True
    rag_rewrite_num_queries: int = 3
    rag_rerank_enabled: bool = True
    rag_rerank_preview_len: int = 200

    # ─── 新增：Memory ───
    memory_short_term_max_turns: int = 10
    memory_long_term_top_k: int = 3
    memory_consolidation_similarity: float = 0.80
    memory_consolidation_dedup: float = 0.95
    memory_consolidation_ttl_days: int = 30
    memory_consolidation_decay_rate: float = 0.995
    memory_consolidation_min_importance: float = 0.3
    memory_consolidation_trigger: int = 5
```

### 2.3 engine.py 切换

**文件**: `backend/app/db/engine.py`  
**改动**:

```python
# 删除：
# SQLite PRAGMA 钩子 (PRAGMA journal_mode=WAL, PRAGMA foreign_keys=ON, busy_timeout=5000)

# 保留：
# create_async_engine, async_sessionmaker, context manager 模式

# 改动：
# database_url 从 "sqlite+aiosqlite:///" 改为 "postgresql+asyncpg://..."
# engine 配置增加 PG 专用参数：pool_size=10, max_overflow=20, pool_pre_ping=True
```

### 2.4 models.py 类型升级 + 新增模型

**文件**: `backend/app/db/models.py`

**改动 1 — JSON 列类型升级**（现有 9 张表）：

```python
# 所有 JSON 列从 Text + json_serializer/deserializer 改为 SQLAlchemy JSONB：
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# 影响的列：
# Agent: capabilities, tool_names
# Conversation: agent_ids, pinned_message_ids, bookmarked_message_ids
# Message: parts, mentioned_agent_ids, usage
# Artifact: content
# AgentRun: dispatch_plan, dispatch_results
# AppSettings: settings
```

**改动 2 — 新增 6 张 ORM 模型**：

```python
class LongTermMemory(Base):
    """从 AGI-memory long_term_memory 表搬运"""
    __tablename__ = "long_term_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(nullable=False, default=0.5)
    embedding: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False)
    last_accessed: Mapped[float] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    slot_hint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    score: Mapped[float] = mapped_column(nullable=False, default=0.0)


class UserPreference(Base):
    """从 AGI-memory user_preferences 表搬运"""
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default="default_user")
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[float] = mapped_column(nullable=False)


class RagChunk(Base):
    """从 AGI-memory rag_chunks 表搬运"""
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_hash: Mapped[str] = mapped_column(String, nullable=False)
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parent_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False)


class ChatHistory(Base):
    """从 AGI-memory chat_history 表搬运（用于 STM 持久化）"""
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False)


class MemoryNode(Base):
    """记忆图节点（GraphMemory 使用，Neo4j 的 PG 镜像表）"""
    __tablename__ = "memory_nodes"

    mem_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(nullable=False, default=0.5)


class MemoryEdge(Base):
    """记忆图边（GraphMemory 使用，Neo4j 的 PG 镜像表）"""
    __tablename__ = "memory_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_id: Mapped[int] = mapped_column(Integer, ForeignKey("memory_nodes.mem_id"), nullable=False)
    to_id: Mapped[int] = mapped_column(Integer, ForeignKey("memory_nodes.mem_id"), nullable=False)
    rel_type: Mapped[str] = mapped_column(String(32), nullable=False)  # FOLLOWS / SIMILAR_TO / CAUSES / BELONGS_TO
    weight: Mapped[float] = mapped_column(nullable=False, default=1.0)
```

### 2.5 验证标准

- [ ] `docker-compose up -d` 全部服务 healthy
- [ ] FastAPI 启动后能连 PG
- [ ] 现有 9 张表通过 `Base.metadata.create_all` 在 PG 中创建
- [ ] 前端能正常对话（API 契约不变）

---

## 3. Phase 1：记忆系统搬运（4-5 天）

### 3.1 ShortTerm — 直接复用

**源**: `待融合项目/AGI-memory/internal/memory/memory.py` 第 75-104 行  
**目标**: `backend/app/memory/short_term.py`

```python
# 搬运 ShortTerm 类（纯内存 deque + RLock）
# 零改动，仅改 import 路径

# 关键实现：
# - deque(maxlen=max_turns*2) 滑动窗口
# - threading.RLock 线程安全
# - add(role, content) 带 timestamp
# - get() / clear() / count()
```

### 3.2 LongTerm — 搬运 + async 改造

**源**: `待融合项目/AGI-memory/internal/memory/memory.py` 第 129-998 行  
**目标**: `backend/app/memory/long_term.py`

**搬运清单**（需要搬运的部分）：

| 方法 | 行数 | 搬运方式 | 说明 |
|------|------|---------|------|
| `Item` 数据类 | 31-43 | A. 直接复用 | 纯数据类 |
| `RecallFilter` | 47-58 | A. 直接复用 | 纯数据类 |
| `ConsolidationResult` | 62-72 | A. 直接复用 | 纯数据类 |
| `_tokenize_zh()` | 107-126 | A. 直接复用 | 纯算法 |
| `_cosine()` (类外) | 各文件 | A. 直接复用 | 纯算法 |
| `load_from_storage()` | 162-197 | **B. async** | psycopg2 → SQLAlchemy async session |
| `add()` | 199-261 | **B. async** | `self.inf.repo.ltm.save` → async db write |
| `recall()` | 263-340 | **B. async** | embedding cosine 不变，DB 查询改 async |
| `recall_by_filter()` | 342-380 | **B. async** | 过滤链不变，调用 recall 改 async |
| `store_classified()` | 382-430 | **B. async** | cosine dedup 不变，写入改 async |
| `consolidate()` | 432-620 | **B. async** | 三阶段算法不变，DB 同步改 async |
| `_merge_pair()` | 622-680 | A. 直接复用 | 纯算法（内容拼接 + emb 加权平均） |
| `_decay()` | 682-700 | A. 直接复用 | 纯算法（指数衰减） |
| `save()` / `load()` | 各 | **B. async** | 全部走 SQLAlchemy async session |

**async 改造模式**（所有 DB 操作统一为此模式）：

```python
# 改造前 (AGI-memory, sync psycopg2):
def load_from_storage(self):
    rows = self.inf.repo.ltm.load()
    self.items = [Item(...) for r in rows]

# 改造后 (AChat, async SQLAlchemy):
async def load_from_storage(self):
    async with get_db() as session:
        stmt = select(LongTermMemory).order_by(LongTermMemory.id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    self.items = [Item(
        content=r.content,
        importance=r.importance,
        embedding=r.embedding,
        created_at=r.created_at,
        last_accessed=r.last_accessed,
        category=r.category,
        tags=list(r.tags),
        slot_hint=r.slot_hint,
        score=r.score,
    ) for r in rows]
    self._next_id = len(self.items)
```

**consolidate 三阶段（核心算法不变，仅 DB 同步改 async）**：

```
阶段 1: 衰减 (decay)
  importance *= decay_rate ^ days_since_creation

阶段 2: 去重 + 合并 (dedup + merge)
  对每对 (i, j): cosine(emb_i, emb_j) >= dedup_threshold → 标记删除
  对每对 (i, j): cosine(emb_i, emb_j) >= similarity_threshold → 合并
    合并策略: content 拼接("；") + embedding 加权平均 + importance max + tags dedup

阶段 3: 淘汰 (expire)
  双条件: age > ttl_days AND importance < min_importance
  图中心度保护: 入度 >= threshold 的节点豁免删除
```

### 3.3 Preference — 搬运 + async 改造

**源**: `待融合项目/AGI-memory/internal/memory/preference.py` (101 行)  
**目标**: `backend/app/memory/preference.py`

```python
# 搬运内容：
# - Preference 类 + RLock 线程安全
# - extract_and_save() 规则提取 (我喜欢/我爱/我叫)
# - build_context() 渲染用户偏好块
# - load_from_storage / set / get / save_batch / snapshot

# async 改造点：
# load_from_storage: self.inf.repo.preference.load → async session query
# set: self.inf.repo.preference.save → async session upsert
# user_id 固定为 "default_user"
```

### 3.4 GraphMemory — 搬运 + async neo4j

**源**: `待融合项目/AGI-memory/internal/memory/graph_memory.py` (404 行)  
**目标**: `backend/app/memory/graph_memory.py`

```python
# 搬运内容：
# - GraphMemory 类
# - 节点操作: _upsert, _update_importance, _delete
# - 边操作: _add_edge (FOLLOWS/SIMILAR_TO/CAUSES/BELONGS_TO)
# - 图遍历: find_related (1-hop 或多跳)
# - 图保护: filter_protected (入度 >= threshold 豁免删除)
# - add_to_graph: _go_safe 后台线程写入

# async 改造：
# neo4j driver → neo4j AsyncDriver
# _go_safe 中的 threading.Thread → asyncio.create_task
# _cosine / _go_safe 等纯工具函数直接复用

# 降级逻辑不变：
# Neo4j 不可用时所有方法 → no-op，不抛异常
```

### 3.5 MemoryService 装配

**新建**: `backend/app/services/memory_service.py`

```python
class MemoryService:
    """三层记忆 + 图增强的装配入口"""

    def __init__(self, settings: Settings, infra: Infrastructure):
        self.stm = ShortTerm(max_turns=settings.memory_short_term_max_turns)
        self.ltm = LongTerm(settings, infra)
        self.preference = Preference(user_id="default_user", infra=infra)
        self.graph_memory = None  # 延迟注入

    async def initialize(self):
        """启动时恢复（对应 AGI-memory restore.py）"""
        await self.ltm.load_from_storage()
        await self.preference.load_from_storage()
        # 图初始化（best-effort）
        if self.infra.neo4j_connected:
            self.graph_memory = GraphMemory(...)
            self.ltm.set_graph_memory(self.graph_memory)
            await self.graph_memory.sync_prev_id()

    async def on_message_end(self, user_msg: str, agent_reply: str):
        """run 结束后的 hook（后台 asyncio.Task）"""
        # 1. 提取事实 → LTM
        facts = extract_facts(agent_reply)  # LLM 提取
        for fact in facts:
            await self.ltm.add(fact)

        # 2. 偏好提取
        self.preference.extract_and_save(user_msg)

        # 3. 触发 consolidate
        if self.ltm.need_consolidation():
            await self.ltm.consolidate()
```

### 3.6 验证标准

- [ ] ShortTerm 单元测试：add/get/clear/sliding window
- [ ] LongTerm 单元测试：add/recall/consolidate（mock embedding）
- [ ] Preference 单元测试：extract_and_save 规则匹配
- [ ] GraphMemory 降级测试：Neo4j 不可用时 no-op
- [ ] MemoryService 集成测试：完整流程（写入→召回→consolidate）

---

## 4. Phase 2：RAG 系统搬运（4-5 天）

### 4.1 RecursiveSplitter — 直接复用

**源**: `待融合项目/AGI-memory/internal/rag/splitter.py` (187 行)  
**目标**: `backend/app/rag/splitter.py`

```python
# 搬运 RecursiveSplitter 类（零改动）
# 核心算法：
# - 递归分隔符栈: ["\n\n", "\n", "。", "！", "？", "；", " ", ""]
# - Markdown 保护: 围栏代码块作为原子片段不可切
# - tail-rune overlap: 相邻 chunk 末尾前缀重叠
# - Chunk 数据类: id + content
```

### 4.2 LLMRewriter — 直接复用

**源**: `待融合项目/AGI-memory/internal/rag/rewriter.py` (99 行)  
**目标**: `backend/app/rag/rewriter.py`

```python
# 搬运 LLMRewriter + HistoryMessage（零改动）
# 核心逻辑：
# - rewrite(query, history) → List[str]（生成 3 条改写变体）
# - system prompt 要求输出严格 JSON {"queries": [...]}
# - 失败降级：返回原 query
# - GenerateFn = Callable[[str, str], str]
```

### 4.3 LLMReranker — 直接复用

**源**: `待融合项目/AGI-memory/internal/rag/reranker.py` (117 行)  
**目标**: `backend/app/rag/reranker.py`

```python
# 搬运 LLMReranker（零改动）
# 核心逻辑：
# - rerank(query, results, top_k) → List（listwise 打分 0-10）
# - 输出 {"scores": [{"idx": 0, "score": 9}, ...]}
# - 失败降级：保持 RRF 原顺序
# - score = llm_score / 10.0
```

### 4.4 HybridStore — 深度适配

**源**: `待融合项目/AGI-memory/internal/rag/hybrid.py` (409 行)  
**目标**: `backend/app/infra/hybrid.py`

**适配要点**：

```python
# 1. 数据类直接复用：
#    HybridResult(pg_id, content, score, source, parent)
#    _PathHits(hits, ok)

# 2. PG 读写改造：
#    self.inf.repo.ragchunk.save_pg_with_parent → SQLAlchemy async session
#    self.inf.repo.ragchunk.load_by_ids_with_parent → async session query

# 3. Milvus 调用不变：
#    self.inf.repo.ragchunk.insert_milvus → pymilvus API
#    self.inf.repo.ragchunk.search_milvus_dicts → pymilvus search

# 4. ES 调用不变：
#    self.inf.repo.ragchunk.index_es → elasticsearch-py API
#    self.inf.repo.ragchunk.search_es_dicts → ES search

# 5. 并发改造：
#    search_multi 中 threading.Thread → asyncio.gather
#    _fetch_milvus / _fetch_es / _fetch_kg → async def

# 6. RRF 融合算法完全不变：
#    score(d) = Σ weight_i / (k + rank_i(d))
#    权重归一：任一路失败时跳过并重新归一化
```

**RRF 核心算法（不改动）**：

```python
def _search_hybrid(self, query, top_k):
    fetch_k = max(top_k * 2, 10)
    milvus_path = await self._fetch_milvus(query, fetch_k)
    es_path = await self._fetch_es(query, fetch_k)
    kg_path = await self._fetch_kg(query, fetch_k)

    # 三路全失败 → 空
    if not any(p.ok for p in [milvus_path, es_path, kg_path]):
        return []

    sem_w = self.settings.rag_semantic_weight
    kw_w = 1.0 - sem_w
    k = self.settings.rag_rrf_constant_k

    rrf_scores = {}
    for rank, hit in enumerate(milvus_path.hits):
        pg_id = hit["pg_id"]
        rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + sem_w / (k + rank + 1)
    for rank, hit in enumerate(es_path.hits):
        pg_id = hit["pg_id"]
        rrf_scores[pg_id] = rrf_scores.get(pg_id, 0.0) + kw_w / (k + rank + 1)
    # ... KG 路同理

    # 排序 + 回查 PG 取完整内容
    sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    rows = await self._load_rows([pid for pid, _ in sorted_ids])
    return [HybridResult(pg_id=pid, content=row.content, score=score, source="hybrid") ...]
```

### 4.5 RAGService 装配

**新建**: `backend/app/services/rag_service.py`

```python
class RAGService:
    """RAG 系统装配入口"""

    def __init__(self, settings: Settings, infra: Infrastructure):
        self.splitter = RecursiveSplitter(
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap,
        )
        self.hybrid_store = HybridStore(settings, infra)
        self.rewriter = LLMRewriter(generate_fn=self._llm_generate)
        self.reranker = LLMReranker(generate_fn=self._llm_generate)
        self.hybrid_store.set_reranker(self.reranker)

    async def ingest(self, doc_hash: str, text: str) -> list[int]:
        """文档切分 → embedding → 入库"""
        chunks = self.splitter.split(text)
        embeddings = await self._embed_batch([c.content for c in chunks])
        parents = self._build_parents(chunks)
        return await self.hybrid_store.index_with_parents(
            doc_hash, [c.content for c in chunks], parents, embeddings
        )

    async def search(self, query: str, top_k: int) -> list[HybridResult]:
        """查询改写 → 多路检索 → RRF 融合 → 精排"""
        queries = self.rewriter.rewrite(query, history=[])
        return await self.hybrid_store.search_multi(queries, top_k)
```

### 4.6 Tool 注册

**文件**: `backend/app/tools/registry.py`

```python
# 新增 3 个工具注册到 tool_registry：

# rag_search: Agent 在对话中搜索知识库
#   input: { query: str, top_k: int }
#   output: List[HybridResult]

# rag_ingest: Agent 把文档灌入知识库
#   input: { doc_hash: str, content: str }
#   output: { chunk_count: int }

# memory_recall: Agent 主动召回长期记忆
#   input: { query: str, top_k: int, categories?: list[str] }
#   output: List[Item]
```

### 4.7 验证标准

- [ ] RecursiveSplitter 单元测试：中英文混合切分 + Markdown 保护
- [ ] LLMRewriter 单元测试：改写 3 条 + 失败降级
- [ ] LLMReranker 单元测试：打分排序 + 失败降级
- [ ] HybridStore 集成测试：Milvus+ES+Neo4j 三路 RRF
- [ ] HybridStore 降级测试：逐路关闭验证 RRF 权重归一
- [ ] RAGService 端到端测试：ingest → search → 结果正确

---

## 5. Phase 3：Prompt 装配器（3-4 天）

### 5.1 数据结构 — 直接复用

从 AGI-memory `promptctx/` 搬运以下数据结构（零改动）：

**slot.py → 内联到 `prompt_assembler.py`**：

```python
# 6 种 SlotKind
SlotProfile = "profile"
SlotPlanner = "planner"
SlotTaskMem = "task_memory"
SlotToolState = "tool_state"
SlotConstraints = "constraints"
SlotRecall = "recall_memory"

# SlotFilter / Slot / ContextItem / FilledSlot 数据类（直接复用）
```

**schema.py → 4 种 Schema（深度适配）**：

```python
# 4 种 Schema 定义（搬运后调整 token 预算值）：
# CHAT_SCHEMA:  Constraints + Profile + Recall
# TOOL_SCHEMA:  Constraints + Profile + ToolState + Recall
# REACT_SCHEMA: Constraints + Planner + TaskMem + ToolState + Profile + Recall
# RAG_SCHEMA:   Constraints + Profile + Recall

# slot_priority() 函数（直接复用）
# DEFAULT_GLOBAL_TOKEN_BUDGET = 2400（字符数）
```

### 5.2 Source 适配 — 深度改造

```python
class ContextSource(ABC):
    """Source 基类（搬运 + async 改造）"""
    @abstractmethod
    async def fetch(self, slot: Slot, q: Query) -> List[ContextItem]:
        ...

# 6 种 Source 需要适配 AChat 数据源：

# ProfileSource: 读 agents 表（Agent.system_prompt + Preference）
#   - AGI-memory 从 PG preference 表读
#   - AChat 从 agents.system_prompt + memory_service.preference 读

# PlannerSource: 读 Orchestrator DAG 状态
#   - AGI-memory 从内存 PlannerSnapshot 读
#   - AChat 从 orchestrator 的 dispatch_plan 读

# TaskMemSource: 读当前子任务的步骤观察
#   - AGI-memory 从内存 StepObservation 列表读
#   - AChat 从 agent_runner 的 run context 读

# ToolStateSource: 读可用工具 + 最近调用结果
#   - AGI-memory 从内存 ToolCallTrace 读
#   - AChat 从 agent_runs 表 + tool_registry 读

# ConstraintsSource: 读 Workspace 沙箱策略
#   - AGI-memory 从 Policy 对象读
#   - AChat 从 workspace.fs_write_approval_mode 读

# RecallSource: 调 memory_service.recall
#   - AGI-memory 直接调 LongTerm.recall_by_filter
#   - AChat 调 memory_service.ltm.recall_by_filter
```

### 5.3 Assembler — async 改造

```python
class ContextAssembler:
    """装配入口（从 AGI-memory 搬运 + async 改造）"""

    async def assemble(self, q: Query) -> RuntimeContext:
        schema = self.schemas.get(q.mode, self.schemas.get("chat"))

        rc = RuntimeContext(schema=schema, filled=[FilledSlot(kind=s.kind) for s in schema.slots])

        # 并发填充：ThreadPoolExecutor → asyncio.gather
        if schema.slots:
            tasks = [self._fill_slot(slot, q) for slot in schema.slots]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    rc.filled[idx] = FilledSlot(kind=schema.slots[idx].kind, skipped=True, reason=str(result))
                else:
                    rc.filled[idx] = result

        self._apply_global_budget(rc)  # 纯内存操作，不变
        return rc

    async def _fill_slot(self, slot: Slot, q: Query) -> FilledSlot:
        sources = self.registry.for_kind(slot.kind)
        all_items = []
        for src in sources:
            items = await src.fetch(slot, q) or []
            all_items.extend(items)
        if slot.filter.token_budget > 0:
            all_items = _trim_by_budget(all_items, slot.filter.token_budget)
        return FilledSlot(kind=slot.kind, items=all_items)
```

### 5.4 集成到 conversation_context.py

**文件**: `backend/app/services/conversation_context.py`

```python
# 改造 build_history_for()：
# 现有: 硬编码拼装 system_prompt + history
# 改造后: 调用 PromptAssembler

async def build_history_for(agent_id, conversation_id, options=None):
    # 1. 构建 Query
    q = Query(
        text=user_message,
        mode="chat",  # 或 "react" / "tool" / "rag"
        embedding=await embed(user_message),
        history=recent_messages,
    )

    # 2. 调用装配器
    ctx = await prompt_assembler.assemble(q)

    # 3. 渲染为 OpenAI chat messages
    system_prompt = ctx.render_system_prompt()
    history = ctx.render_history()

    # 保持 BuildHistoryOptions 接口不变（向后兼容）
    return history
```

### 5.5 验证标准

- [ ] 4 种 Schema 装配测试
- [ ] 各 Source 独立测试 + 降级测试
- [ ] token 预算裁剪测试（全局 + 单槽位）
- [ ] 集成测试：完整对话 → PromptAssembler 输出正确

---

## 6. Phase 4：AgentRunner 集成（2-3 天）

### 6.1 build_adapter_input 改造

**文件**: `backend/app/services/agent_runner.py`

```python
# 改造点：build_adapter_input()
# 现有：
#   system_prompt = agent.system_prompt
#   history = build_history_for(agent, conv)

# 改造后：
#   ctx = await prompt_assembler.assemble(Query(...))
#   system_prompt = ctx.render_system_prompt()  # 包含记忆/RAG/工具状态
#   history = ctx.render_history()              # 带 token 预算裁剪
```

### 6.2 finalize 加记忆 hook

**文件**: `backend/app/services/agent_runner.py`

```python
# 改造点：finalize() — run 结束后的后处理
# 现有：持久化最后一条消息 + 发 RunEndEvent

# 新增 hook（后台 asyncio.Task，不阻塞返回）：
async def _post_run_memory_hook(user_msg: str, agent_reply: str):
    try:
        await memory_service.on_message_end(user_msg, agent_reply)
    except Exception as e:
        logger.warning("post-run memory hook failed: %s", e)

# 在 finalize() 中：
# asyncio.create_task(_post_run_memory_hook(user_msg, agent_reply))
```

### 6.3 main.py 启动流程

**文件**: `backend/app/main.py`

```python
# 启动时增加：

@app.on_event("startup")
async def startup():
    # 1. 现有初始化（DB 连接等）
    await init_db()

    # 2. 新增：构建 Infrastructure
    infra = await build_infrastructure(get_settings())
    logger.info(infra.status_summary())

    # 3. 新增：初始化 MemoryService
    memory_service = MemoryService(get_settings(), infra)
    await memory_service.initialize()

    # 4. 新增：初始化 RAGService
    rag_service = RAGService(get_settings(), infra)

    # 5. 新增：注册 PromptAssembler
    prompt_assembler = build_prompt_assembler(get_settings(), memory_service, rag_service)
```

### 6.4 Infrastructure 工厂

**新建**: `backend/app/infra/factory.py`

```python
@dataclass
class Infrastructure:
    """基础设施连接管理（async 版，参考 AGI-memory infra.py）"""
    milvus_client: Any = None
    es_client: Any = None
    neo4j_driver: Any = None
    kafka_producer: Any = None

    milvus_connected: bool = False
    es_connected: bool = False
    neo4j_connected: bool = False
    kafka_connected: bool = False

    def status_summary(self) -> str:
        parts = []
        parts.append(f"Milvus: {'✅' if self.milvus_connected else '⚠️ disconnected'}")
        parts.append(f"ES: {'✅' if self.es_connected else '⚠️ disconnected'}")
        parts.append(f"Neo4j: {'✅' if self.neo4j_connected else '⚠️ disconnected'}")
        parts.append(f"Kafka: {'✅' if self.kafka_connected else '⚠️ disconnected'}")
        return " | ".join(parts)


async def build_infrastructure(settings: Settings) -> Infrastructure:
    """配置驱动装配，每个连接独立 try/except"""
    infra = Infrastructure()

    # Milvus
    if settings.milvus_host:
        try:
            from pymilvus import MilvusClient
            infra.milvus_client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")
            infra.milvus_connected = True
            logger.info("✅ Milvus connected: %s:%d", settings.milvus_host, settings.milvus_port)
        except Exception as e:
            logger.warning("⚠️  Milvus 连接失败: %s", e)

    # Elasticsearch
    if settings.es_addresses:
        try:
            from elasticsearch import AsyncElasticsearch
            infra.es_client = AsyncElasticsearch(hosts=settings.es_addresses.split(","))
            await infra.es_client.info()
            infra.es_connected = True
        except Exception as e:
            logger.warning("⚠️  ES 连接失败: %s", e)

    # Neo4j
    if settings.neo4j_uri:
        try:
            from neo4j import AsyncGraphDatabase
            infra.neo4j_driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
            )
            await infra.neo4j_driver.verify_connectivity()
            infra.neo4j_connected = True
        except Exception as e:
            logger.warning("⚠️  Neo4j 连接失败: %s", e)

    # Kafka
    if settings.kafka_brokers:
        try:
            from kafka import KafkaProducer
            infra.kafka_producer = KafkaProducer(bootstrap_servers=settings.kafka_brokers.split(","))
            infra.kafka_connected = True
        except Exception as e:
            logger.warning("⚠️  Kafka 连接失败: %s", e)

    return infra
```

### 6.5 验证标准

- [ ] 端到端对话：发消息 → Agent 回复 → 记忆自动写入 LTM
- [ ] 跨会话记忆：新对话中 LLM 能召回上次对话的记忆
- [ ] 偏好提取：用户说"我喜欢 Python" → Preference 存储
- [ ] consolidate 触发：存入 N 条后自动 consolidate
- [ ] 启动日志：正确输出各组件连接状态

---

## 7. Phase 5：测试 + 打磨（3-5 天）

### 7.1 降级测试矩阵

```
逐服务 kill → 验证对应能力降级 + 系统继续运行：

kill milvus:
  ✅ 向量路关闭 → RRF 变为 ES 单路或 Neo4j 单路
  ✅ 记忆 recall 降级为 TF cosine
  ✅ AChat 完全可用

kill elasticsearch:
  ✅ BM25 路关闭 → RRF 变为 Milvus 单路
  ✅ AChat 完全可用

kill neo4j:
  ✅ 图路关闭 → RRF 变为 Milvus+ES 双路
  ✅ GraphMemory → no-op
  ✅ consolidate 不再做图中心度保护
  ✅ AChat 完全可用

kill kafka:
  ✅ 事件只走 InProcess
  ✅ AChat 完全可用

kill postgres:
  ❌ PG 是主存储，挂了系统不可用
  （可接受：PG 稳定性远超 SQLite，且可做主从复制）
```

### 7.2 性能基准

```
测试项                         预期
─────────────────────────────────────
PG JSONB 查询 (messages.parts)  < 10ms (1000 条消息)
Milvus ANN 搜索 (1024 维)      < 50ms (10 万向量)
ES BM25 + IK (中文)            < 30ms (10 万文档)
Neo4j 1-hop 遍历               < 20ms (1000 节点)
RRF 融合 (三路 top 10)         < 100ms 总计
PromptAssembler 并发填充        < 200ms (6 Source)
Memory recall (embedding cosine) < 50ms (1000 条记忆)
consolidate (500 条记忆)        < 5s
```

### 7.3 数据迁移脚本

为现有 SQLite 用户编写迁移工具：

```python
# scripts/migrate_sqlite_to_pg.py
# 读取 .agenthub-data/agenthub.db
# 逐表导入到 PostgreSQL
# 处理 JSON 列格式差异
# 验证数据完整性
```

### 7.4 启动状态仪表盘

启动日志输出示例：

```
══════════════════════════════════════════════════════
  AChat Infrastructure Status
══════════════════════════════════════════════════════
  PostgreSQL:    ✅ connected (asyncpg, pool=10)
  Milvus:        ✅ connected (standalone, dim=1024)
  Elasticsearch: ✅ connected (8.14.0, IK analyzer)
  Neo4j:         ✅ connected (5-community, APOC)
  Kafka:         ⚠️  not configured (optional)
══════════════════════════════════════════════════════
  Memory:        STM(deque) + LTM(PG+Milvus) + Preference(PG) + Graph(Neo4j)
  RAG:           HybridStore(Milvus+ES+Neo4j, 3-way RRF)
  Prompt:        Assembler(6 sources, budget=2400 chars)
  EventBus:      InProcess(SSE) + Kafka(disabled)
══════════════════════════════════════════════════════
  Server:        http://127.0.0.1:8000
  Frontend:      http://localhost:3000
══════════════════════════════════════════════════════
```

---

## 8. 依赖管理

### 8.1 新增 Python 依赖

```
# backend/requirements.txt 新增：

# PostgreSQL async driver
asyncpg>=0.29.0
sqlalchemy[postgresql_asyncpg]>=2.0

# Milvus
pymilvus>=2.4.0

# Elasticsearch
elasticsearch[async]>=8.14.0

# Neo4j
neo4j>=5.0.0

# Kafka（可选）
kafka-python>=2.0.0

# Embedding（如果没有现成 API）
# 通过 CustomAdapter 复用现有 LLM API，无需额外依赖
```

### 8.2 资源需求

```
最低配置（全部服务）：
  内存: ~6-8GB
    ├── ES: ~1.5GB
    ├── Milvus: ~1GB
    ├── Neo4j: ~1GB
    ├── Kafka+ZK: ~1GB (可选)
    ├── PG: ~500MB
    └── 应用: ~500MB
  磁盘: ~5GB+
  启动时间: ~30-60 秒
```

---

## 9. 降级链路总览

```
组件              正常状态           降级状态                    影响
─────────────────────────────────────────────────────────────────────
PostgreSQL        主存储             ❌ 不可降级（hard dep）     系统不可用
Milvus            向量 ANN           TF cosine (内存暴力搜索)    检索质量下降
Elasticsearch     BM25+IK            无全文检索                  中文检索质量下降
Neo4j             知识图谱           GraphMemory no-op           无图增强
Kafka             事件总线           仅 InProcess                SSE 不受影响
Embedding API     向量化             TF-IDF 近似                 记忆召回质量下降
LLM API           改写/精排          跳过 rewrite/rerank         检索精度下降
```

---

## 10. 不做的事情（明确排除）

- ❌ 用户认证/授权/RBAC
- ❌ 多租户数据隔离
- ❌ 计费/配额/审计系统
- ❌ 前端登录页/用户管理 UI
- ❌ API 鉴权中间件
- ❌ 离线模式保留
- ❌ 数据迁移（可选，非必须）
- ❌ AGI-memory 的 agent/router.py（AChat 用 Adapter 选择替代）
- ❌ AGI-memory 的 handler/handler.py（AChat 有 FastAPI 路由层）
- ❌ AGI-memory 的 sandbox/*（AChat 有自己的 workspace 沙箱）
- ❌ AGI-memory 的 tools/tools.py（AChat 有自己的 tool registry）
- ❌ AGI-memory 的 platform/*（SQLAlchemy 统一了 DB 访问）

---

## 11. 快速参考：AGI-memory 关键算法参数

```python
# ─── RRF 融合 ───
rrf_constant_k = 60             # RRF 常数
semantic_weight = 0.7           # 语义路权重
keyword_weight = 0.3            # 关键词路权重 (= 1 - semantic - kg)

# ─── 记忆 consolidate ───
similarity_threshold = 0.80     # 合并阈值
dedup_threshold = 0.95          # 去重阈值
ttl_days = 30                   # 过期天数
decay_rate = 0.995              # 每日衰减系数
min_importance = 0.3            # 最低重要性
trigger_interval = 5            # 每 N 条触发 consolidate

# ─── RAG ───
chunk_size = 200                # 切分大小
chunk_overlap = 50              # 重叠大小
top_k = 3                       # 默认返回数
rag_milvus_dim = 1024           # 向量维度
rewrite_num_queries = 3         # 改写条数
rerank_preview_len = 200        # 精排预览长度

# ─── 记忆 ───
short_term_max_turns = 10       # 短期窗口轮数
long_term_top_k = 3             # 长期召回数
cosine_dedup_on_store = 0.95    # 写入前去重

# ─── 图 ───
kg_max_hops = 2                 # 图遍历跳数
kg_weight = 0.3                 # 图路 RRF 权重
graph_protect_indegree = 3      # 图中心度保护阈值

# ─── Prompt 装配器 ───
global_token_budget = 2400      # 全局字符预算
slot_budget_constraints = 200
slot_budget_profile = 300
slot_budget_recall = 400
slot_budget_tool_state = 350
slot_budget_planner = 300
slot_budget_task_mem = 350
```

---

## 12. 实施检查清单

### Phase 0 ✅
- [ ] docker-compose.yml 创建 + 全部服务 healthy
- [ ] config.py 扩展配置字段
- [ ] engine.py 切 asyncpg
- [ ] models.py JSONB 升级 + 新增 6 张表
- [ ] 现有 API 在 PG 上正常运行

### Phase 1 ✅
- [ ] ShortTerm 搬运 + 测试
- [ ] LongTerm 搬运 + async 改造 + 测试
- [ ] Preference 搬运 + async 改造 + 测试
- [ ] GraphMemory 搬运 + async 改造 + 降级测试
- [ ] MemoryService 装配 + 集成测试

### Phase 2 ✅
- [ ] RecursiveSplitter 搬运 + 测试
- [ ] LLMRewriter 搬运 + 测试
- [ ] LLMReranker 搬运 + 测试
- [ ] HybridStore 搬运 + async 改造 + 降级测试
- [ ] RAGService 装配 + 端到端测试
- [ ] 3 个 RAG Tool 注册 + 测试

### Phase 3 ✅
- [ ] 6 种 SlotKind + 4 种 Schema 搬运
- [ ] 6 种 Source 适配 AChat 数据源
- [ ] ContextAssembler async 改造
- [ ] 集成到 conversation_context.py
- [ ] token 预算裁剪测试

### Phase 4 ✅
- [ ] build_adapter_input 对接 PromptAssembler
- [ ] finalize 加记忆写入 hook
- [ ] main.py 启动流程加 Infrastructure
- [ ] 端到端验证：对话 → 记忆写入 → 跨会话召回

### Phase 5 ✅
- [ ] 降级测试（逐服务 kill）
- [ ] 性能基准测试
- [ ] 数据迁移脚本（可选）
- [ ] 启动状态仪表盘
- [ ] 全量回归测试
