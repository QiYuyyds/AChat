# 双数据库架构设计：SQLite（本地）+ PostgreSQL（远端）

> **状态**：设计提案（待审阅）
> **日期**：2026-07-22
> **作者**：架构讨论
> **关联 spec**：`specs/08-db-schema.md`、`specs/11-platform.md`、`openspec/specs/persistence/spec.md`

---

## 1. 背景与动机

### 1.1 当前痛点

系统当前使用单一 PostgreSQL 作为持久化层。在「基础设施远端部署 + 用户本地部署前后端」的场景下，每次数据库操作都要经历 **50ms RTT** 的网络往返。

问题在流式响应热路径上最为严重：

```
token 到达 → persist_event() → 写 PG (50ms) 或 XADD Redis Stream (50ms) → 前端 SSE publish
```

即使引入了 Redis Stream 做 write-behind 异步写入：

1. Redis 本身也在远端，`XADD` 仍然有 50ms 延迟
2. 消费者批量写 PG 又是一次 50ms 往返
3. 整条链路：`token → XADD(50ms) → 消费者 flush PG(50ms) → publish`
4. 虽然比直接同步写 PG 快了，但仍远不如全部本地部署

### 1.2 核心洞察

当前 21 张表的访问模式可以明确分为两类：

| 模式 | 特征 | 典型表 | 延迟要求 |
|---|---|---|---|
| **高频写 / 延迟敏感** | 每个 token 都写、运行中频繁更新 | `messages`、`agent_runs` | < 1ms |
| **低频读 / RAG 依赖** | 一轮对话查一次、需要 Milvus/ES/Neo4j | `rag_chunks`、`long_term_memory` | 50ms 可接受 |

**关键发现**：如果把高频表放在本地 SQLite（0.1ms RTT），低频表留在远端 PG（50ms RTT），则：

- 热路径 per-token 写入：50ms → 0.1ms = **500 倍提升**
- 冷路径 RAG 检索：50ms 不变（可接受，一轮对话只查一次）
- 本地模式下可彻底移除 Redis 依赖（SQLite 直写够快）

### 1.3 目标

- [x] 消除本地部署场景下对 Redis 的硬依赖
- [x] per-token 消息持久化延迟 < 1ms
- [x] RAG / 记忆系统不受影响（仍走远端 PG + Milvus/ES/Neo4j）
- [x] 服务器部署模式行为完全不变（单 PG，向后兼容）
- [x] 用户对话数据留在本地（隐私收益）

---

## 2. 架构总览

### 2.1 架构图

```
┌──────────────────────────────────────────────────────┐
│              本地 SQLite (0.1ms RTT)                  │
│                                                       │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐     │
│  │  messages  │ │conversations│ │  agent_runs  │     │
│  │(per-token  │ │ (metadata)  │ │  (status)    │     │
│  │  writes!) │ │             │ │              │     │
│  └────────────┘ └────────────┘ └──────────────┘     │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐     │
│  │ artifacts  │ │ workspaces │ │ attachments  │     │
│  └────────────┘ └────────────┘ └──────────────┘     │
│  ┌────────────────────┐ ┌────────────────────┐      │
│  │context_summaries   │ │run_checkpoints     │      │
│  └────────────────────┘ └────────────────────┘      │
│                                                       │
│  ❌ 不需要 Redis Stream（SQLite 直写够快）            │
│  ❌ 不需要 Redis KV Cache（本地读取够快）             │
└──────────────────────────────────────────────────────┘
          │ agent_id (无 FK 约束，App 层校验)
          │ user_id  (无 FK 约束，App 层校验)
          ▼
┌──────────────────────────────────────────────────────┐
│           远端 PostgreSQL (50ms RTT, 可接受)          │
│                                                       │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐     │
│  │   users    │ │   agents   │ │ mcp_servers  │     │
│  │(auth only) │ │(cacheable) │ │              │     │
│  └────────────┘ └────────────┘ └──────────────┘     │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐     │
│  │ rag_chunks │ │ltm_memory  │ │ chat_history  │     │
│  │ → Milvus   │ │ → Milvus   │ │ → Milvus     │     │
│  └────────────┘ └────────────┘ └──────────────┘     │
│  ┌────────────────────┐ ┌────────────────────┐      │
│  │memory_nodes/edges  │ │ documents/versions │      │
│  │ → Neo4j            │ │ → RAG pipeline     │      │
│  └────────────────────┘ └────────────────────┘      │
│  ┌────────────────────┐ ┌────────────────────┐      │
│  │  user_settings     │ │  user_preferences  │      │
│  └────────────────────┘ └────────────────────┘      │
│  ┌────────────────────┐ ┌────────────────────┐      │
│  │ global_settings     │ │  app_settings      │      │
│  └────────────────────┘ └────────────────────┘      │
└──────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────┐
│        基础设施层 (与 PG 同机房, 50ms RTT OK)         │
│        Milvus · Elasticsearch · Neo4j                 │
└──────────────────────────────────────────────────────┘
```

### 2.2 部署模式对照

| 模式 | 本地 DB | 远端 DB | Redis | 适用场景 |
|---|---|---|---|---|
| **服务器部署**（当前） | 无 | PG（全部 21 张表） | 可选 | 多用户、集中式 |
| **双 DB 本地**（新增） | SQLite（8 张高频表） | PG（13 张低频表） | 不需要 | 用户本地跑前后端 + 远端基础设施 |
| **纯本地**（理想态） | SQLite（全部） | 无 | 不需要 | 桌面端、离线使用 |

本文档聚焦 **双 DB 本地** 模式。

---

## 3. 模型分类

### 3.1 本地 SQLite 表（8 张）— 高频 / 延迟敏感

| 模型 | 表名 | 写入频率 | 为什么放本地 |
|---|---|---|---|
| `Message` | `messages` | **每个 token** | 核心热路径，per-token parts 更新 |
| `AgentRun` | `agent_runs` | 运行中高频 | status 频繁变更（queued→running→complete） |
| `AgentRunCheckpoint` | `agent_run_checkpoints` | 运行中 | save/resume 检查点 |
| `Conversation` | `conversations` | 每轮对话 | updated_at、summary 等频繁更新 |
| `Workspace` | `workspaces` | 低频但需快速读 | 路径解析在工具调用热路径上 |
| `Attachment` | `attachments` | 低频 | 文件引用，与 message 同生命周期 |
| `ContextSummary` | `conversation_context_summaries` | 压缩时 | 与 conversation 同生命周期 |
| `Artifact` | `artifacts` | 低频但需快速读 | 产物元数据，预览时频繁查 |

### 3.2 远端 PostgreSQL 表（13 张）— 低频 / RAG 依赖 / 可缓存

| 模型 | 表名 | 依赖 | 为什么放远端 |
|---|---|---|---|
| `RagChunk` | `rag_chunks` | Milvus | 向量检索需要与 Milvus 同机房 |
| `LongTermMemory` | `long_term_memory` | Milvus | 同上 |
| `ChatHistory` | `chat_history` | Milvus | LTM embedding 检索 |
| `MemoryNode` | `memory_nodes` | Neo4j | 知识图谱节点 |
| `MemoryEdge` | `memory_edges` | Neo4j | 知识图谱边 |
| `Document` | `documents` | RAG pipeline | 文档元数据 + 解析入库 |
| `DocumentVersion` | `document_versions` | RAG pipeline | 版本化管理 |
| `User` | `users` | — | 仅登录时查（JWT 无状态） |
| `Agent` | `agents` | — | 每 run 查一次，已有 Redis 缓存 |
| `McpServer` | `mcp_servers` | — | 极少查询 |
| `UserSettings` | `user_settings` | — | 可缓存（5min TTL） |
| `UserPreference` | `user_preferences` | — | 极少查询 |
| `GlobalSettings` | `global_settings` | — | 单例，可缓存 |
| `AppSettings` | `app_settings` | — | 极少查询 |

### 3.3 分类原则总结

```
判定公式：
  如果表在 per-token 热路径上       → SQLite（本地）
  如果表需要 Milvus / ES / Neo4j    → PostgreSQL（远端）
  如果表可缓存且低频访问             → PostgreSQL（远端）+ Redis 缓存
```

---

## 4. 跨数据库关系处理

### 4.1 当前 FK 关系分析

模型间有以下外键关系（`→` 表示 FK 指向）：

**同库 FK（不跨 DB，保持不变）：**

```
messages.conversation_id        → conversations.id        (SQLite 内部)
messages.parent_message_id      → (无 FK，已是无约束列)
agent_runs.conversation_id      → conversations.id        (SQLite 内部)
agent_runs.parent_run_id        → (无 FK，已是无约束列)
artifacts.conversation_id       → conversations.id        (SQLite 内部)
attachments.conversation_id     → conversations.id        (SQLite 内部)
context_summaries.conversation_id → conversations.id      (SQLite 内部)
workspaces.conversation_id      → conversations.id        (SQLite 内部)
rag_chunks.document_id          → documents.id            (PG 内部)
rag_chunks.version_id           → document_versions.id    (PG 内部)
long_term_memory.user_id        → users.id                (PG 内部)
```

**跨库 FK（需要移除 FK 约束）：**

```
messages.agent_id           → agents.id        (SQLite → PostgreSQL) ❌ 跨库
agent_runs.agent_id         → agents.id        (SQLite → PostgreSQL) ❌ 跨库
conversations.user_id      → users.id          (SQLite → PostgreSQL) ❌ 跨库
```

只有 **3 个** 跨库 FK 需要处理。

### 4.2 处理策略

**移除跨库 FK 约束，改为纯 String 列：**

```python
# models.py — 修改前
class Conversation(Base):
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), name="user_id", nullable=False
    )

class Message(Base):
    agent_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agents.id"), name="agent_id", nullable=True
    )

class AgentRun(Base):
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id"), name="agent_id", nullable=False
    )
```

```python
# models.py — 修改后（双 DB 模式）
class Conversation(Base):
    user_id: Mapped[str] = mapped_column(
        String, name="user_id", nullable=False  # 无 ForeignKey
    )

class Message(Base):
    agent_id: Mapped[str | None] = mapped_column(
        String, name="agent_id", nullable=True  # 无 ForeignKey
    )

class AgentRun(Base):
    agent_id: Mapped[str] = mapped_column(
        String, name="agent_id", nullable=False  # 无 ForeignKey
    )
```

**安全性分析**：App 层已通过 `user_id` 过滤做数据隔离（所有查询都带 `WHERE user_id = ?`），不依赖 DB 级 FK 约束。移除 FK 不影响数据完整性。

### 4.3 ORM relationship 调整

跨库的 ORM `relationship()` 需要改为手动查询，避免 SQLAlchemy 尝试跨库 join：

```python
# 修改前：ORM 自动 lazy-load（会尝试跨库 join，报错）
class Message(Base):
    agent: Mapped["Agent | None"] = relationship(back_populates="messages")

# 修改后：禁止 lazy-load，改为手动查询
class Message(Base):
    agent: Mapped["Agent | None"] = relationship(
        back_populates="messages",
        lazy="raise",  # 阻止隐式跨库加载
    )

# 调用方手动查 Agent（已有 get_agent_cached 可用）：
agent = await get_agent_cached(msg.agent_id)  # 走远端 PG + Redis 缓存
```

### 4.4 JSON 类型兼容性

当前代码已有兼容方案（`models.py` 第 22-25 行）：

```python
from sqlalchemy.types import JSON as _BaseJSON
JSONB = _BaseJSON  # PG dialect → JSONB, SQLite dialect → JSON
```

两个 DB 的 JSON 列完全兼容，**无需改模型字段定义**。

---

## 5. 引擎与会话管理

### 5.1 当前架构（单引擎）

```python
# engine.py（当前）
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

async def init_db():
    _engine = create_async_engine(settings.database_url, ...)
    _session_factory = async_sessionmaker(bind=_engine, ...)

async def get_db() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session
        await session.commit()
```

所有服务函数统一用 `async with get_db() as db:` 获取 session。

### 5.2 目标架构（双引擎）

```python
# engine.py（修改后）
_local_engine: AsyncEngine | None = None       # SQLite（高频表）
_remote_engine: AsyncEngine | None = None      # PostgreSQL（低频表）
_local_session_factory: async_sessionmaker | None = None
_remote_session_factory: async_sessionmaker | None = None

# 模型 → 引擎路由表
_LOCAL_TABLES = {
    "messages", "conversations", "agent_runs", "agent_run_checkpoints",
    "artifacts", "workspaces", "attachments", "conversation_context_summaries",
}

async def init_db():
    settings = get_settings()

    # 远端 PG（始终需要）
    _remote_engine = create_async_engine(settings.database_url, ...)
    _remote_session_factory = async_sessionmaker(
        bind=_remote_engine, class_=AsyncSession,
        expire_on_commit=False, autoflush=False,
    )

    # 本地 SQLite（仅当配置了 database_local_url 时）
    if settings.database_local_url:
        _local_engine = create_async_engine(
            settings.database_local_url, echo=False, future=True
        )
        # SQLite 需要启用 WAL + FK cascade
        @event.listens_for(_local_engine.sync_engine, "connect")
        def _init_sqlite(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        _local_session_factory = async_sessionmaker(
            bind=_local_engine, class_=AsyncSession,
            expire_on_commit=False, autoflush=False,
        )

    # 分别 create_all（只创建对应引擎上的表）
    from app.db.models import Base
    from app.db.table_routing import get_local_models, get_remote_models

    if _local_engine:
        async with _local_engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=[t for m in get_local_models() for t in [m.__table__]]
                )
            )
    async with _remote_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=[t for m in get_remote_models() for t in [m.__table__]]
            )
        )
    # ... _migrate_columns 也要按库分别执行
```

### 5.3 Session 获取函数

```python
@asynccontextmanager
async def get_local_db() -> AsyncIterator[AsyncSession]:
    """高频数据 session（SQLite）。

    单 DB 模式（服务器部署）时回退到 remote session，
    保持与当前 get_db() 完全一致的行为。
    """
    factory = _local_session_factory or _remote_session_factory
    if factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_remote_db() -> AsyncIterator[AsyncSession]:
    """知识/RAG 数据 session（PostgreSQL）。"""
    if _remote_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _remote_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# 向后兼容别名：单 DB 模式时 get_db = get_remote_db
get_db = get_remote_db
```

### 5.4 配置变更

#### `config.py`

```python
class Settings(BaseSettings):
    # ... 现有字段 ...

    # 远端 PostgreSQL（知识/RAG/配置表）
    database_url: str = "postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub"

    # 本地 SQLite（高频对话表）
    # 设置后启用双 DB 模式；None = 单 DB 模式（服务器部署，向后兼容）
    database_local_url: str | None = None
```

#### `.env.example` 新增

```bash
# ═══════════════════════════════════════════════════════════
# Dual Database (optional — local/desktop deployment)
# ═══════════════════════════════════════════════════════════
# When set, high-frequency tables (messages, conversations, agent_runs,
# artifacts, workspaces, attachments, context_summaries, run_checkpoints)
# are stored in local SQLite for <1ms latency.
# Low-frequency tables (RAG, memory, knowledge graph, config) remain in
# the remote PostgreSQL above.
# Leave unset for server deployment (all tables in PostgreSQL).
# DATABASE_LOCAL_URL=sqlite+aiosqlite:///./.agenthub-data/local.db
```

---

## 6. 服务层改造

### 6.1 改造原则

```
改 get_db() → get_local_db()  ：当操作的是高频表（messages/conversations/agent_runs/...）
改 get_db() → get_remote_db()：当操作的是低频表（agents/users/rag_chunks/ltm/...）
不改                           ：当操作混合表时（需拆分为两个 session）
```

### 6.2 热路径：`agent_runner.py`

#### `persist_event` 函数

当前实现（简化版）：

```python
async def persist_event(event, parts_buffer, run_id, agent_id, ...):
    redis_client = _get_redis_client()
    use_stream = redis_client is not None

    if etype == "message.start":
        if use_stream:
            await xadd_event(redis_client, run_id, ...)  # 50ms RTT
            return
        async with get_db() as db:                      # 50ms RTT
            db.add(Message(...))
        return

    if etype == "part.delta":
        await _persist_or_stream(redis_client, run_id, event, parts, use_stream)
        # ↑ XADD(50ms) 或 get_db()(50ms)
        return
```

修改后（双 DB 模式）：

```python
async def persist_event(event, parts_buffer, run_id, agent_id, ...):
    # 双 DB 模式下，本地 SQLite 直写足够快，不需要 Redis Stream
    if _local_session_factory is not None:
        # ── 本地 SQLite 直写路径（0.1ms）──
        if etype == "message.start":
            async with get_local_db() as db:            # 0.1ms!
                db.add(Message(...))
            return

        if etype == "part.delta":
            async with get_local_db() as db:            # 0.1ms!
                await db.execute(
                    update(Message)
                    .where(Message.id == message_id)
                    .values(parts=parts)
                )
            return

        if etype == "message.end":
            async with get_local_db() as db:
                await db.execute(
                    update(Message)
                    .where(Message.id == message_id)
                    .values(status="complete", parts=parts)
                )
            return

        # ... 其他事件类型同理，全部 get_local_db() 直写

    else:
        # ── 单 DB 模式（服务器部署），保留原有 Redis Stream 逻辑 ──
        redis_client = _get_redis_client()
        use_stream = redis_client is not None
        # ... 原有逻辑不变
```

#### `_persist_or_stream` 函数

```python
async def _persist_or_stream(redis_client, run_id, event, parts, use_stream, ...):
    # 双 DB 模式：直接写本地 SQLite
    if _local_session_factory is not None:
        async with get_local_db() as db:
            if message_id:
                await db.execute(
                    update(Message)
                    .where(Message.id == message_id)
                    .values(parts=parts)
                )
        return

    # 单 DB 模式：保留原有 Redis Stream / 同步写 PG 逻辑
    if use_stream and redis_client is not None:
        try:
            await xadd_event(redis_client, run_id, json.dumps(event_data))
            return
        except Exception as e:
            logger.warning("XADD failed, falling back to sync: %s", e)

    async with get_db() as db:
        # ... 同步写 PG
```

### 6.3 冷路径：`cache_helpers.py`

`cache_helpers.py` 中的缓存函数操作的是远端表（`Agent`、`UserSettings`、`GlobalSettings`），需要改用 `get_remote_db()`：

```python
# 修改前
async def get_agent_cached(agent_id: str) -> Agent | None:
    async def _load():
        async with get_db() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            ...

# 修改后
async def get_agent_cached(agent_id: str) -> Agent | None:
    async def _load():
        async with get_remote_db() as db:           # ← 改这里
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            ...
```

同理，`get_workspace_cached` 需要改用 `get_local_db()`（因为 `Workspace` 是高频本地表）：

```python
# 修改后
async def get_workspace_cached(conversation_id: str) -> Workspace | None:
    async def _load():
        async with get_local_db() as db:            # ← 本地表
            result = await db.execute(
                select(Workspace).where(Workspace.conversation_id == conversation_id)
            )
            ...
```

### 6.4 各 API 路由改造

以下是需要检查和改造的 API 路由文件及其使用的表：

| 文件 | 主要操作的表 | 目标 session |
|---|---|---|
| `api/conversations.py` | conversations, messages, agent_runs | `get_local_db()` |
| `api/messages.py` | messages | `get_local_db()` |
| `api/agents.py` | agents | `get_remote_db()` |
| `api/artifacts.py` | artifacts | `get_local_db()` |
| `api/auth.py` | users | `get_remote_db()` |
| `api/documents.py` | documents, rag_chunks | `get_remote_db()` |
| `api/mcp.py` | mcp_servers | `get_remote_db()` |
| `api/profile.py` | user_preferences, user_settings | `get_remote_db()` |
| `api/settings.py` | global_settings, app_settings | `get_remote_db()` |
| `api/workspaces.py` | workspaces | `get_local_db()` |
| `api/memory.py` | long_term_memory, chat_history | `get_remote_db()` |

### 6.5 服务层改造

| 文件 | 主要操作的表 | 目标 session |
|---|---|---|
| `services/agent_runner.py` | messages, agent_runs | `get_local_db()` |
| `services/agent_loop.py` | messages, agent_runs | `get_local_db()` |
| `services/conversation_service.py` | conversations, messages | `get_local_db()` |
| `services/orchestrator.py` | messages, agent_runs | `get_local_db()` |
| `services/tool_executor.py` | workspaces | `get_local_db()` |
| `services/rag_service.py` | rag_chunks, documents | `get_remote_db()` |
| `services/memory_service.py` | long_term_memory, chat_history | `get_remote_db()` |
| `services/settings_service.py` | user_settings, global_settings | `get_remote_db()` |
| `services/compact_pipeline.py` | context_summaries, messages | `get_local_db()` |
| `services/document_service.py` | documents, document_versions | `get_remote_db()` |

---

## 7. Redis 依赖变更

### 7.1 本地双 DB 模式下 Redis 的角色

| Redis 功能 | 当前用途 | 双 DB 后 | 原因 |
|---|---|---|---|
| **KV Cache** (`cache_helpers.py`) | 缓存 Agent/UserSettings/GlobalSettings | **保留**（仅远端表） | Agent 在远端 PG，仍需缓存 |
| **KV Cache** (`get_workspace_cached`) | 缓存 Workspace | **移除** | Workspace 在本地 SQLite，读取 0.1ms |
| **Stream write-behind** (`async_db_writer.py`) | 异步批量写 messages | **移除** | SQLite 直写 0.1ms，不需要缓冲 |
| **Stream crash recovery** (`recovery_scan.py`) | 恢复中断的 streaming 消息 | **简化** | 本地 SQLite WAL 模式自带崩溃恢复 |
| **SSE pub/sub** | 全局 SSE 事件广播 | **保留** | SSE 是跨连接广播机制，与 DB 无关 |
| **Rate limiting** | API 限流 | **保留** | 与 DB 无关 |

### 7.2 `async_db_writer.py` 改造

```python
# 双 DB 模式下，DBWriterConsumer 不启动
async def start_db_writer(redis_client) -> None:
    if _local_session_factory is not None:
        logger.info("Dual-DB mode: SQLite direct write active, skipping DBWriterConsumer")
        return
    # ... 原有逻辑
    global _writer_instance
    _writer_instance = DBWriterConsumer(redis_client)
    await _writer_instance.start()
```

### 7.3 `recovery_scan.py` 改造

```python
async def scan_interrupted_messages() -> int:
    if _local_session_factory is not None:
        # 双 DB 模式：SQLite WAL 自带崩溃恢复，只需扫描 stuck streaming 消息
        return await _scan_local_stuck_messages()
    # ... 原有逻辑（Redis Stream 回放）
```

SQLite 的 WAL 模式保证了已提交事务的持久性。中断的 `streaming` 状态消息直接标记为 `interrupted` 即可，不需要从 Redis Stream 回放。

### 7.4 `main.py` 启动流程改造

```python
# main.py lifespan（修改后）
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 现有初始化 ...

    # 初始化数据库（双引擎或单引擎）
    await init_db()

    # 判断是否双 DB 模式
    from app.db.engine import _local_session_factory
    is_dual_db = _local_session_factory is not None

    # Redis 初始化
    _infrastructure = await init_infrastructure()
    redis_client = _infrastructure.redis_client if _infrastructure else None

    if redis_client is not None:
        if not is_dual_db:
            # 单 DB 模式：启动 Redis Stream 消费者
            from app.services.async_db_writer import start_db_writer
            await start_db_writer(redis_client)

    # 崩溃恢复
    from app.services.recovery_scan import scan_interrupted_messages
    await scan_interrupted_messages()

    yield

    # 关闭
    if redis_client is not None and not is_dual_db:
        from app.services.async_db_writer import stop_db_writer
        await stop_db_writer()

    await close_db()
```

---

## 8. 数据迁移

### 8.1 单 DB → 双 DB 迁移

当用户从服务器部署切换到本地双 DB 部署时，需要将 8 张高频表的数据从 PG 导入到本地 SQLite。

**迁移脚本** `scripts/migrate_to_dual_db.py`：

```python
"""将高频表从 PostgreSQL 迁移到本地 SQLite。

用法：
    DATABASE_URL=postgresql+asyncpg://remote-server/agenthub \
    DATABASE_LOCAL_URL=sqlite+aiosqlite:///./local.db \
    python scripts/migrate_to_dual_db.py
"""

LOCAL_TABLES = [
    "messages", "conversations", "agent_runs", "agent_run_checkpoints",
    "artifacts", "workspaces", "attachments", "conversation_context_summaries",
]

async def migrate():
    # 1. 在本地 SQLite 上 create_all（创建 8 张表）
    # 2. 遍历每张表：
    #    - 从 PG SELECT * 批量读取（分页，每批 1000 行）
    #    - INSERT INTO SQLite（使用 INSERT OR IGNORE 避免重复）
    # 3. 验证行数一致
    ...
```

### 8.2 双 DB → 单 DB 回滚

如果需要回滚到单 PG 模式：

1. 设置 `DATABASE_LOCAL_URL=`（空值或不设置）
2. 运行反向迁移脚本：将 SQLite 中的 8 张表数据导回 PG
3. 重启后端

### 8.3 数据一致性

双 DB 模式下，两张 DB 之间**没有事务一致性保证**。但分析各场景：

| 场景 | 风险 | 评估 |
|---|---|---|
| 写 message 时远端 Agent 已被删除 | `agent_id` 指向不存在的 Agent | App 层已有 `get_agent_cached` 返回 None 的处理 |
| 写 conversation 时远端 User 已被删除 | `user_id` 指向不存在的 User | User 删除是极低频操作，且会级联清理本地数据 |
| 本地 SQLite 损坏 | 消息丢失 | SQLite WAL 模式 + 定期备份（workspace 目录下） |

---

## 9. 深入：SQLite 的适用性分析

### 9.1 并发写入

SQLite WAL 模式支持 **多读 + 单写** 并发：

- 多个 SSE 连接可以同时读（不阻塞）
- 写操作串行化（通过 `busy_timeout=5000` 等待）
- 单用户的 Agent 运行不会产生并发写冲突（消息是追加到同一个 parts 数组，而非多行并发插入）

### 9.2 数据量

单用户的消息量估算：

| 指标 | 估算 |
|---|---|
| 日均对话轮次 | ~50 轮 |
| 每轮消息数 | ~3 条（user + agent + tool） |
| 每条消息 parts 大小 | ~10KB |
| 日数据量 | ~1.5MB |
| 年数据量 | ~550MB |

SQLite 单文件上限为 **2TB**（远超需求）。加上定期清理 / 归档机制，不存在容量问题。

### 9.3 性能基准

| 操作 | PostgreSQL（远端 50ms RTT） | SQLite（本地） |
|---|---|---|
| INSERT 一行 | ~50ms | ~0.1ms |
| UPDATE parts（JSON 列） | ~50ms | ~0.1ms |
| SELECT by PK | ~50ms | ~0.05ms |
| 批量 SELECT | ~50ms | ~1ms |

---

## 10. 测试策略

### 10.1 单元测试

现有测试使用 SQLite 作为测试 DB。双 DB 改造后：

- **单 DB 测试**（现有）：不变，`DATABASE_LOCAL_URL` 不设置
- **双 DB 测试**（新增）：设置 `DATABASE_LOCAL_URL=sqlite:///test_local.db`，验证跨库行为

### 10.2 关键测试用例

```
test_dual_db_message_persistence
    → 验证 message 写入 SQLite 而非 PG

test_dual_db_agent_cached_from_remote
    → 验证 Agent 从远端 PG 读取 + Redis 缓存

test_dual_db_conversation_user_id_no_fk
    → 验证无 FK 约束下 user_id 仍正确隔离

test_dual_db_redis_stream_not_started
    → 验证双 DB 模式下 DBWriterConsumer 不启动

test_dual_db_recovery_scan_local
    → 验证 SQLite WAL 下的 stuck message 恢复

test_single_db_backward_compat
    → 验证不设置 DATABASE_LOCAL_URL 时行为与当前完全一致
```

### 10.3 集成测试

```python
# pytest fixture
@pytest.fixture
async def dual_db_env(monkeypatch):
    """启用双 DB 模式的测试环境。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test-remote/agenthub")
    monkeypatch.setenv("DATABASE_LOCAL_URL", "sqlite+aiosqlite:///./test_local.db")
    # ... init_db, yield, teardown
```

---

## 11. 改造文件清单

### 11.1 必须修改的文件

| 文件 | 改动内容 | 改动量 |
|---|---|---|
| `backend/app/config.py` | 新增 `database_local_url` 配置项 | ~3 行 |
| `backend/app/db/engine.py` | 双引擎初始化 + `get_local_db()` / `get_remote_db()` | ~60 行 |
| `backend/app/db/models.py` | 移除 3 个跨库 FK 约束 + relationship lazy="raise" | ~10 行 |
| `backend/app/db/table_routing.py` | 新建：模型 → 引擎路由表 | ~30 行 |
| `backend/app/services/agent_runner.py` | `persist_event` / `_persist_or_stream` 双 DB 分支 | ~50 行 |
| `backend/app/services/async_db_writer.py` | `start_db_writer` 双 DB 模式跳过 | ~5 行 |
| `backend/app/services/recovery_scan.py` | `scan_interrupted_messages` 双 DB 简化 | ~15 行 |
| `backend/app/infra/cache_helpers.py` | `get_workspace_cached` 改用 `get_local_db` | ~3 行 |
| `backend/app/main.py` | lifespan 启动逻辑条件分支 | ~10 行 |
| `backend/.env.example` | 新增 `DATABASE_LOCAL_URL` 注释 | ~8 行 |

### 11.2 需要审查的文件（批量 `get_db()` → `get_local_db()` / `get_remote_db()`）

| 文件 | 数量 | 目标 |
|---|---|---|
| `backend/app/api/conversations.py` | ~15 处 | `get_local_db` |
| `backend/app/api/messages.py` | ~5 处 | `get_local_db` |
| `backend/app/api/agents.py` | ~10 处 | `get_remote_db` |
| `backend/app/api/artifacts.py` | ~5 处 | `get_local_db` |
| `backend/app/api/auth.py` | ~3 处 | `get_remote_db` |
| `backend/app/api/documents.py` | ~8 处 | `get_remote_db` |
| `backend/app/api/mcp.py` | ~3 处 | `get_remote_db` |
| `backend/app/api/profile.py` | ~5 处 | `get_remote_db` |
| `backend/app/api/settings.py` | ~3 处 | `get_remote_db` |
| `backend/app/api/workspaces.py` | ~3 处 | `get_local_db` |
| `backend/app/api/memory.py` | ~5 处 | `get_remote_db` |
| `backend/app/services/conversation_service.py` | ~10 处 | `get_local_db` |
| `backend/app/services/agent_loop.py` | ~5 处 | `get_local_db` |
| `backend/app/services/orchestrator.py` | ~8 处 | `get_local_db` |
| `backend/app/services/tool_executor.py` | ~3 处 | `get_local_db` |
| `backend/app/services/rag_service.py` | ~5 处 | `get_remote_db` |
| `backend/app/services/memory_service.py` | ~5 处 | `get_remote_db` |
| `backend/app/services/settings_service.py` | ~5 处 | `get_remote_db` |
| `backend/app/services/compact_pipeline.py` | ~5 处 | `get_local_db` |
| `backend/app/services/document_service.py` | ~5 处 | `get_remote_db` |

### 11.3 新建文件

| 文件 | 用途 |
|---|---|
| `backend/app/db/table_routing.py` | 模型分类常量 + 路由辅助函数 |
| `scripts/migrate_to_dual_db.py` | 单 DB → 双 DB 数据迁移脚本 |

---

## 12. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| 跨库 FK 移除导致数据不一致 | 低 | 中 | App 层已有 user_id 隔离校验；Agent 删除时级联清理本地 messages |
| SQLite 并发写锁竞争 | 低 | 低 | WAL 模式 + busy_timeout=5000；单用户场景无高并发 |
| 本地 SQLite 文件损坏 | 极低 | 高 | WAL 模式保证持久性；定期备份到 workspace 目录 |
| 服务函数遗漏改造（仍用 `get_db`） | 中 | 低 | `get_db` 别名指向 `get_remote_db`，高频表写远端只是慢，不会出错 |
| 双 DB 事务边界问题 | 低 | 中 | 当前代码中不存在跨表事务（每个 `get_db()` 块只操作同类表） |
| 迁移脚本数据丢失 | 中 | 高 | 分页迁移 + 行数校验 + 回滚脚本 |

---

## 13. 实施计划

### Phase 1：基础设施（1-2 天）

- [ ] 新建 `table_routing.py` 模型分类常量
- [ ] 改造 `engine.py` 支持双引擎初始化
- [ ] 改造 `config.py` 新增 `database_local_url`
- [ ] 更新 `.env.example`
- [ ] 改造 `models.py` 移除跨库 FK
- [ ] 编写双 DB 模式单元测试

### Phase 2：热路径改造（1-2 天）

- [ ] 改造 `persist_event` / `_persist_or_stream` 双 DB 分支
- [ ] 改造 `async_db_writer.py` 条件跳过
- [ ] 改造 `recovery_scan.py` 本地模式简化
- [ ] 改造 `main.py` lifespan 启动逻辑
- [ ] 改造 `cache_helpers.py` 路由

### Phase 3：服务层批量改造（2-3 天）

- [ ] 逐文件审查 `get_db()` 调用，改为 `get_local_db()` / `get_remote_db()`
- [ ] 优先改造热路径文件（`agent_runner`、`conversation_service`、`agent_loop`）
- [ ] 冷路径文件可后续渐进式改造（`get_db` 别名保证不报错）

### Phase 4：迁移与测试（1-2 天）

- [ ] 编写 `migrate_to_dual_db.py` 迁移脚本
- [ ] 端到端测试：双 DB 模式下完整 Agent 运行
- [ ] 性能基准测试：对比单 DB vs 双 DB 的 per-token 延迟
- [ ] 回归测试：单 DB 模式向后兼容

### Phase 5：文档同步（0.5 天）

- [ ] 更新 `specs/08-db-schema.md`
- [ ] 更新 `openspec/specs/persistence/spec.md`
- [ ] 更新 `CLAUDE.md` §3.1 五层分层说明
- [ ] 更新 `backend/.env.example`

---

## 14. 开放问题（待讨论）

1. **本地 SQLite 备份策略**：是否需要自动备份？频率？是否跟随 workspace 目录一起同步？
2. **多设备同步**：用户在两台电脑上使用时，本地 SQLite 如何同步？（可考虑 workspace 级别的文件同步）
3. **Agent 删除级联**：远端 Agent 被删除时，本地 messages 中的 `agent_id` 变为悬空引用。是否需要主动清理？还是保留（历史消息仍可展示）？
4. **pg_insert 兼容性**：`async_db_writer.py` 和 `recovery_scan.py` 中使用了 `sqlalchemy.dialects.postgresql.insert`（PG 专有的 ON CONFLICT 语法），在 SQLite 上不可用。需要替换为 SQLite 的 `INSERT OR IGNORE` 或 `ON CONFLICT DO NOTHING`。
5. **桌面端集成**：Electron 打包时 SQLite 文件路径如何管理？是否放在 `app.getPath('userData')` 下？
6. **服务器多用户**：服务器部署模式下，是否也考虑给每个用户一个独立的本地 SQLite？（可能过度设计，PG 连接池已经够用）

---

## 附录 A：现有降级机制与双 DB 的兼容性

当前系统已有完善的降级机制，双 DB 模式天然适配：

| 现有降级路径 | 双 DB 下的行为 |
|---|---|
| Redis 不可用 → `persist_event` 回退同步写 PG | 双 DB 下 SQLite 直写，根本不依赖 Redis |
| Milvus 不可用 → RAG 退化为 TF cosine | 不受影响（Milvus 只关联远端 PG 的 rag_chunks） |
| Neo4j 不可用 → GraphMemory no-op | 不受影响（Neo4j 只关联远端 PG 的 memory_nodes） |
| ES 不可用 → 无全文检索 | 不受影响 |
| Phoenix 不可用 → OTel 静默丢弃 | 不受影响 |

**结论**：双 DB 模式与现有降级机制完全兼容，甚至**简化了降级路径**——本地模式下不再需要 Redis 降级逻辑。

---

## 附录 B：SQLite WAL 模式说明

WAL（Write-Ahead Logging）是 SQLite 的高并发模式：

- **读不阻塞写，写不阻塞读**
- 写操作先写入 WAL 文件，定期 checkpoint 合并到主 DB 文件
- 崩溃恢复：WAL 中已提交的事务在下次打开 DB 时自动重放
- `busy_timeout=5000`：写锁竞争时等待 5 秒而非立即报错

当前代码已配置（`engine.py` 第 49-53 行）：

```python
cursor.execute("PRAGMA foreign_keys=ON")
cursor.execute("PRAGMA journal_mode=WAL")
cursor.execute("PRAGMA busy_timeout=5000")
```

双 DB 模式复用此配置，无需额外改动。
