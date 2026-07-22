# 双数据库架构设计：SQLite（本地）+ PostgreSQL（远端）

> **状态**：已实施
> **日期**：2026-07-22
> **作者**：架构讨论
> **关联 spec**：`specs/08-db-schema.md`、`specs/11-platform.md`、`openspec/specs/persistence/spec.md`

---

## 1. 背景与动机

### 1.1 当前痛点

系统当前使用单一 PostgreSQL 作为持久化层。在「基础设施远端部署 + 用户本地部署前后端」的场景下，每次数据库操作都要经历 **50ms RTT** 的网络往返。

> **前置说明**：`optimize-stream-persistence` 变更（已完成 29/29 tasks）已将 `publish()` 提前到 `persist_event()` 之前执行，`message.start/end` 和 `part.delta` 走 Redis Stream write-behind，`usage` 事件走 fire-and-forget `asyncio.create_task`。因此 **SSE 推送不再被 DB 写入阻塞**。当前剩余的延迟瓶颈在于：当 Redis 也在远端时，`XADD` 仍有 50ms RTT。

当前热路径（`optimize-stream-persistence` 之后）：

```
token 到达
  │
  ├─→ publish(event)                ← 立即推 SSE（0ms，asyncio.Queue，已不阻塞）
  │
  └─→ persist_event(event)
        ├─ message.start/end → XADD Redis Stream（远端时 50ms RTT）
        ├─ part.delta        → XADD Redis Stream（远端时 50ms RTT）
        └─ run.usage          → asyncio.create_task（fire-and-forget）

  ↓ 消费者批量 flush

  DBWriterConsumer → 批量 INSERT/UPDATE PG（远端时 50ms RTT）
```

即使 `publish` 不再阻塞，仍有两个问题：

1. Redis 本身在远端时，`XADD` 仍然有 50ms 延迟（写 buffer 的 RTT）
2. 消费者批量写 PG 又是一次 50ms 往返（最终落盘的 RTT）
3. 整条链路：`token → XADD(50ms) → 消费者 flush PG(50ms)`，端到端 100ms 才落盘
4. 虽然前端 SSE 已不卡顿，但消息落盘延迟仍远不如全部本地部署

**双 DB 方案的替代价值**：如果将热数据放本地 SQLite（0.1ms RTT），则 `XADD` 和消费者 flush 两层间接层都不需要，直写 0.1ms 落盘。

### 1.2 核心洞察

当前 22 张表的访问模式可以按**数据归属和依赖关系**明确分类：

| 模式 | 特征 | 典型表 | 延迟要求 |
|---|---|---|---|
| **对话热数据 + 个人本地配置** | per-token 写入、用户本地创建 | `messages`、`agents` | < 1ms |
| **用户系统 + 知识/RAG 数据** | 统一管理、依赖 Milvus/ES/Neo4j | `users`、`rag_chunks` | 50ms 可接受 |

**关键发现**：如果把对话数据和个人配置放在本地 SQLite（0.1ms RTT），用户系统和知识数据留在远端 PG（50ms RTT），则：

- 热路径 per-token 写入：50ms → 0.1ms = **500 倍提升**
- 冷路径 RAG 检索 / 用户认证：50ms 不变（可接受）
- 本地模式下可**彻底移除 Redis 依赖**（SQLite 直写够快，远端 Redis 缓存无意义）

### 1.3 目标

- [x] **隐私**：用户对话数据和 Agent 配置留在本地（一等目标，非副产品）
- [x] 消除本地部署场景下对 Redis 的硬依赖（本地 Redis 也免装）
- [x] per-token 消息持久化延迟 < 1ms（本地 SQLite 直写 0.1ms）
- [x] RAG / 记忆系统不受影响（仍走远端 PG + Milvus/ES/Neo4j）
- [x] 用户系统统一管理（认证 / API Key 跨设备共享）
- [x] 服务器部署模式行为完全不变（单 PG，向后兼容）

> **延迟预期说明**：per-token 持久化延迟 < 1ms 仅适用于 SQLite 写入路径。Agent **run 启动**时仍需从远端 PG 读取 `UserSettings`（API Key）和 `UserPreference` 等冷数据（~50ms），但这是一轮对话只查一次的冷路径开销，不影响流式推送。
>
> **替代方案对比**：纯性能角度，本地部署 Redis（~5MB）+ 现有 Redis Stream 架构也能把 `XADD` 降到 0.1ms，且零代码改动。双 DB 方案的不可替代价值在于**数据隐私**——对话数据不出本机。

---

## 2. 架构总览

### 2.1 架构图

```
┌──────────────────────────────────────────────────────────┐
│              本地 SQLite (0.1ms RTT)                      │
│                                                           │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │   messages      │ │conversations │ │  agent_runs   │  │
│  │  (per-token     │ │  (metadata)  │ │   (status)   │  │
│  │   writes!)     │ │              │ │              │  │
│  └────────┬────────┘ └──────────────┘ └───────────────┘  │
│           │ agent_id (FK)                                  │
│           ▼                                                │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │    agents       │ │ mcp_servers  │ │  artifacts    │  │
│  │ (user-created)  │ │(user-config) │ │               │  │
│  └─────────────────┘ └──────────────┘ └───────────────┘  │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │  workspaces     │ │ attachments  │ │context_summ.  │  │
│  └─────────────────┘ └──────────────┘ └───────────────┘  │
│  ┌─────────────────┐                                    │  │
│  │ run_checkpoints │                                    │  │
│  └─────────────────┘                                    │  │
│                                                           │
│  ❌ 不需要 Redis Stream（SQLite 直写够快）                │
│  ❌ 不需要 Redis KV Cache（本地读取够快）                 │
│  ✅ EventBus 已用 asyncio.Queue（从未用 Redis pub/sub）  │
└──────────────────────────────────────────────────────────┘
          │ user_id (无 FK 约束，App 层 JWT 校验)
          │   conversations.user_id     → users.id  (SQLite → PG)
          │   agents.user_id            → users.id  (SQLite → PG)
          │   mcp_servers.user_id       → users.id  (SQLite → PG)
          ▼
┌──────────────────────────────────────────────────────────┐
│           远端 PostgreSQL (50ms RTT, 可接受)              │
│                                                           │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │    users        │ │user_settings │ │user_pref.     │  │
│  │  (auth: JWT)    │ │ (API keys)  │ │               │  │
│  └─────────────────┘ └──────────────┘ └───────────────┘  │
│  ┌─────────────────┐ ┌──────────────┐                      │
│  │ global_settings │ │ app_settings │                      │
│  └─────────────────┘ └──────────────┘                      │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │  rag_chunks     │ │ltm_memory    │ │ chat_history  │  │
│  │  → Milvus       │ │ → Milvus     │ │ → Milvus     │  │
│  └─────────────────┘ └──────────────┘ └───────────────┘  │
│  ┌─────────────────────────┐ ┌─────────────────────────┐  │
│  │ memory_nodes / edges    │ │ documents / versions   │  │
│  │ → Neo4j                │ │ → RAG pipeline         │  │
│  └─────────────────────────┘ └─────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────┐
│        基础设施层 (与 PG 同机房, 50ms RTT OK)             │
│        Milvus · Elasticsearch · Neo4j                     │
└──────────────────────────────────────────────────────────┘
```

### 2.2 部署模式对照

| 模式 | 本地 DB | 远端 DB | Redis | 适用场景 |
|---|---|---|---|---|
| **服务器部署**（当前） | 无 | PG（全部 22 张表） | 可选 | 多用户、集中式 |
| **双 DB 本地**（新增） | SQLite（10 张表） | PG（12 张表） | **不需要** | 用户本地跑前后端 + 远端基础设施 |
| **纯本地**（理想态） | SQLite（全部 22 张） | 无 | 不需要 | 桌面端、离线使用 |

本文档聚焦 **双 DB 本地** 模式。

### 2.3 为什么 Redis 在远端时缓存无意义

在远端基础设施部署场景下，Redis 和 PG 都在远端服务器上：

```
查 Redis KV Cache（远端）→ 50ms RTT
查 PostgreSQL（远端）   → 50ms RTT
```

两者延迟相同，Redis 缓存命中**不比直接查 PG 快**。只有当 Redis 部署在本地时（0.1ms），缓存才有意义。但我们已经排除了桌面端打包 Redis 的方案。

因此：**远端 Redis KV 缓存 = 无用的间接层**。

---

## 3. 模型分类

### 3.1 本地 SQLite 表（10 张）— 对话热数据 + 个人本地配置

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
| `Agent` | `agents` | 用户创建时 | 用户通过 Agent Builder **本地创建**的个人配置，不需要统一管理 |
| `McpServer` | `mcp_servers` | 用户配置时 | 用户**本地配置**的 MCP 服务，不属于系统级配置 |

### 3.2 远端 PostgreSQL 表（12 张）— 用户系统 + 知识/RAG 数据

| 模型 | 表名 | 依赖 | 为什么放远端 |
|---|---|---|---|
| `User` | `users` | — | 用户认证统一管理，跨设备一致 |
| `UserSettings` | `user_settings` | — | API Key 等设置需跨设备共享 |
| `UserPreference` | `user_preferences` | — | 用户偏好档案，属于用户系统 |
| `GlobalSettings` | `global_settings` | — | 系统级配置，不属于某台设备 |
| `AppSettings` | `app_settings` | — | 应用级配置 |
| `RagChunk` | `rag_chunks` | Milvus | 向量检索需要与 Milvus 同机房 |
| `LongTermMemory` | `long_term_memory` | Milvus | 同上 |
| `ChatHistory` | `chat_history` | Milvus | LTM embedding 检索 |
| `MemoryNode` | `memory_nodes` | Neo4j | 知识图谱节点 |
| `MemoryEdge` | `memory_edges` | Neo4j | 知识图谱边 |
| `Document` | `documents` | RAG pipeline | 文档元数据 + 解析入库 |
| `DocumentVersion` | `document_versions` | RAG pipeline | 版本化管理 |

### 3.3 分类原则总结

```
判定公式：
  用户系统 / 系统级配置           → 远端 PG（统一管理，跨设备共享）
  用户本地创建的个人配置           → 本地 SQLite（设备私有，不需要统一）
  依赖 Milvus / ES / Neo4j 的表   → 远端 PG（与基础设施同机房）
  per-token 热写入的对话数据       → 本地 SQLite（< 1ms 延迟）
```

关键区分：**Agent 和 McpServer 是用户自己创建/配置的，属于个人本地配置，不需要放远端统一管理**。而 User、UserSettings、GlobalSettings 是用户系统的一部分，需要跨设备统一管理。

---

## 4. 跨数据库关系处理

### 4.1 当前 FK 关系分析

模型间有以下外键关系（`→` 表示 FK 指向）：

**SQLite 内部 FK（不跨 DB，保持不变）：**

```
messages.conversation_id            → conversations.id        (SQLite 内部, ondelete=CASCADE)
messages.agent_id                   → agents.id               (SQLite 内部) ✅ 原来跨库，现在同库！
agent_runs.conversation_id          → conversations.id        (SQLite 内部, ondelete=CASCADE)
agent_runs.agent_id                 → agents.id               (SQLite 内部) ✅ 原来跨库，现在同库！
artifacts.conversation_id           → conversations.id        (SQLite 内部, ondelete=CASCADE)
artifacts.created_by_agent_id       → agents.id               (SQLite 内部) ← 文档初版遗漏
attachments.conversation_id         → conversations.id        (SQLite 内部, ondelete=CASCADE)
context_summaries.conversation_id   → conversations.id        (SQLite 内部, ondelete=CASCADE)
workspaces.conversation_id          → conversations.id        (SQLite 内部, ondelete=CASCADE)
agent_run_checkpoints.run_id        → agent_runs.id           (SQLite 内部, ondelete=CASCADE) ← 文档初版遗漏
```

> **注意**：多个 FK 带 `ondelete="CASCADE"` 语义。SQLite 的 `PRAGMA foreign_keys=ON`（已在 `engine.py` 中配置）确保 CASCADE 生效。如果将来修改此 PRAGMA，级联删除会静默失效。

**PG 内部 FK（不跨 DB，保持不变）：**

```
user_settings.user_id              → users.id                (PG 内部, ondelete=CASCADE)
documents.user_id                  → users.id                (PG 内部)
document_versions.document_id      → documents.id            (PG 内部, ondelete=CASCADE)
rag_chunks.document_id             → documents.id            (PG 内部, ondelete=SET NULL)
rag_chunks.version_id              → document_versions.id    (PG 内部, ondelete=SET NULL)
long_term_memory.user_id           → users.id                (PG 内部)
chat_history.user_id               → users.id                (PG 内部)
memory_nodes.user_id               → users.id                (PG 内部)
memory_edges.from_id               → memory_nodes.mem_id     (PG 内部)
memory_edges.to_id                 → memory_nodes.mem_id     (PG 内部)
```

**跨库 FK（需要移除 FK 约束）：**

```
conversations.user_id      → users.id          (SQLite → PostgreSQL) ❌ 跨库
agents.user_id              → users.id          (SQLite → PostgreSQL) ❌ 跨库
mcp_servers.user_id         → users.id          (SQLite → PostgreSQL) ❌ 跨库
```

只有 **3 个** 跨库 FK 需要处理，且**模式统一**——全部是 `user_id → users.id`，处理方式完全一致。

> **注意**：`messages.agent_id` 和 `agent_runs.agent_id` 在原方案中是跨库 FK，但修正后 Agent 移到 SQLite，这两个 FK 变成 SQLite 内部 FK，**可以保留不动**。这是一个重要的架构优势。

### 4.2 处理策略

**移除跨库 FK 约束（仅 `user_id → users.id`），改为纯 String 列：**

```python
# models.py — 修改前
class Conversation(Base):
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), name="user_id", nullable=False
    )

class Agent(Base):
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), name="user_id", nullable=True
    )

class McpServer(Base):
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), name="user_id", nullable=True
    )
```

```python
# models.py — 修改后（双 DB 模式）
class Conversation(Base):
    user_id: Mapped[str] = mapped_column(
        String, name="user_id", nullable=False  # 无 ForeignKey
    )

class Agent(Base):
    user_id: Mapped[str | None] = mapped_column(
        String, name="user_id", nullable=True  # 无 ForeignKey
    )

class McpServer(Base):
    user_id: Mapped[str | None] = mapped_column(
        String, name="user_id", nullable=True  # 无 ForeignKey
    )
```

**安全性分析**：

- `user_id` 来自 JWT 认证上下文，不是用户请求体传入的，不存在伪造
- 所有查询都带 `WHERE user_id = ?` 过滤，App 层做数据隔离
- User 删除是极低频操作，删除时会级联清理用户数据
- 移除 FK 不影响数据完整性

### 4.3 ORM relationship 调整

由于 Agent 在 SQLite 本地，`Message → Agent` 的 ORM relationship 是**同库**的，**不需要修改**：

```python
# 不变！Agent 和 Message 都在 SQLite，同库 relationship 正常工作
class Message(Base):
    agent: Mapped["Agent | None"] = relationship(back_populates="messages")
    # ✅ 同库（SQLite），FK 保留，relationship 正常 lazy-load
```

需要调整的是跨库方向的 relationship（如果有）。当前代码中：

```python
class Conversation(Base):
    # 无 relationship 指向 User（当前代码就没有定义这个）
    # user_id 只是纯列，没有 ORM 关系，无需调整
```

**结论**：ORM relationship 层面**几乎不需要改动**，因为跨库引用都是纯 `user_id` 字符串列，没有定义 ORM relationship。

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
_local_engine: AsyncEngine | None = None       # SQLite（对话热数据 + 个人配置）
_remote_engine: AsyncEngine | None = None      # PostgreSQL（用户系统 + 知识/RAG）
_local_session_factory: async_sessionmaker | None = None
_remote_session_factory: async_sessionmaker | None = None

# 本地表名集合（10 张）
_LOCAL_TABLES = {
    "messages", "conversations", "agent_runs", "agent_run_checkpoints",
    "artifacts", "workspaces", "attachments", "conversation_context_summaries",
    "agents", "mcp_servers",
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
    from app.db.table_routing import get_local_table_objects, get_remote_table_objects

    if _local_engine:
        async with _local_engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=get_local_table_objects()
                )
            )
    async with _remote_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=get_remote_table_objects()
            )
        )
    # _migrate_columns 也要按库分别执行：
    # - PG 专有语法（ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... ::jsonb）
    #   仅在 remote_engine 上执行
    # - SQLite 需要独立的迁移路径（SQLite 不支持 ADD COLUMN IF NOT EXISTS
    #   和 ::jsonb 类型转换；create_all 已创建新表新列，旧表新增列靠
    #   try/except 包裹的 ALTER TABLE，失败即跳过）
    if _local_engine:
        async with _local_engine.begin() as conn:
            await conn.run_sync(_migrate_columns_sqlite)
    async with _remote_engine.begin() as conn:
        await conn.run_sync(_migrate_columns_pg)
```

### 5.3 Session 获取函数

```python
@asynccontextmanager
async def get_local_db() -> AsyncIterator[AsyncSession]:
    """本地数据 session（SQLite）。

    操作的表：messages, conversations, agent_runs, agents, mcp_servers,
    artifacts, workspaces, attachments, context_summaries, run_checkpoints

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
    """远端数据 session（PostgreSQL）。

    操作的表：users, user_settings, user_preferences, global_settings,
    app_settings, rag_chunks, long_term_memory, chat_history,
    memory_nodes, memory_edges, documents, document_versions
    """
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

    # 远端 PostgreSQL（用户系统 + 知识/RAG 数据）
    database_url: str = "postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub"

    # 本地 SQLite（对话热数据 + 个人本地配置）
    # 设置后启用双 DB 模式；None = 单 DB 模式（服务器部署，向后兼容）
    database_local_url: str | None = None
```

#### `.env.example` 新增

```bash
# ═══════════════════════════════════════════════════════════
# Dual Database (optional — local/desktop deployment)
# ═══════════════════════════════════════════════════════════
# When set, conversation data and personal config tables are stored
# in local SQLite for <1ms latency:
#   messages, conversations, agent_runs, agents, mcp_servers,
#   artifacts, workspaces, attachments, context_summaries, run_checkpoints
#
# User system and knowledge/RAG tables remain in the remote PostgreSQL:
#   users, user_settings, user_preferences, global_settings, app_settings,
#   rag_chunks, long_term_memory, chat_history, memory_nodes, memory_edges,
#   documents, document_versions
#
# Redis is NOT required in dual-DB mode.
# Leave unset for server deployment (all tables in PostgreSQL).
# DATABASE_LOCAL_URL=sqlite+aiosqlite:///./.agenthub-data/local.db
```

---

## 6. 服务层改造

### 6.1 改造原则

```
改 get_db() → get_local_db()  ：当操作本地表（messages/conversations/agents/mcp_servers/...）
改 get_db() → get_remote_db()：当操作远端表（users/rag_chunks/ltm/documents/...）
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

### 6.3 `cache_helpers.py` 改造

修正后 Agent 在本地 SQLite，`get_agent_cached` 直接读本地即可，**不再需要 Redis 缓存**：

```python
# 修改前：Agent 在远端 PG，走 Redis KV 缓存
async def get_agent_cached(agent_id: str) -> Agent | None:
    cache = get_cache()
    key = f"agent:{agent_id}"
    async def _load():
        async with get_db() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            ...
    data = await cache.get_or_load(key, AGENT_TTL, _load)

# 修改后：Agent 在本地 SQLite，直接读即可（0.1ms），不需要 Redis 缓存
async def get_agent_cached(agent_id: str) -> Agent | None:
    async with get_local_db() as db:            # ← 0.1ms 本地直读
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()
```

同理，`get_workspace_cached` 也改为本地直读：

```python
# 修改后：Workspace 在本地 SQLite
async def get_workspace_cached(conversation_id: str) -> Workspace | None:
    async with get_local_db() as db:            # ← 0.1ms 本地直读
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()
```

#### `get_user_settings_cached` / `get_global_settings_cached` 的处理

这两个实体留在远端 PG，**不能简化为本地 SQLite 直读**。它们当前走 Redis KV 缓存（5min TTL），双 DB 模式下有两种处理方式：

**方案 A：直接查远端 PG（推荐，简单）**

```python
async def get_user_settings_cached(user_id: str) -> UserSettings | None:
    async with get_remote_db() as db:          # ← 远端 PG 直读（50ms）
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        return result.scalar_one_or_none()
```

设计文档的论证是：远端 Redis 缓存命中也是 50ms RTT，与直接查 PG 相同，所以缓存无延迟优势。`UserSettings` 主要在 `build_adapter_input`（run 启动时读 API Key）和 `api/settings.py`（前端设置面板）两处调用，都是冷路径（一轮对话一次 / 面板打开一次），50ms 可接受。

**方案 B：进程内 dict 缓存（可选，零延迟）**

如果后续发现 50ms 远端 PG 读在 run 启动路径上仍有感知，可引入一个简单的进程内 TTL 缓存（不依赖 Redis）：

```python
_process_cache: dict[str, tuple[Any, float]] = {}  # key → (value, expire_ts)

async def get_user_settings_cached(user_id: str) -> UserSettings | None:
    key = f"user_settings:{user_id}"
    now = time.time()
    cached = _process_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]                               # ← 0ms 进程内命中
    async with get_remote_db() as db:
        result = await db.execute(...)
        row = result.scalar_one_or_none()
        if row is not None:
            _process_cache[key] = (row, now + 300)     # 5min TTL
        return row
```

> **注意**：进程内缓存仅适用于单 worker 场景（本地部署）。多 worker 服务器部署仍应用 Redis。`invalidate_*_cache` 函数改为同时清进程内 dict 和 Redis（如果 Redis 可用）。

#### 哪些实体从未被 Redis 缓存

以下实体**当前就不走 Redis KV 缓存**，双 DB 改造不改变其行为：

| 实体 | 当前读取方式 | 双 DB 后 | 是否需要新增缓存 |
|---|---|---|---|
| `LongTermMemory` | recall 路径：**进程内存**（`LongTermMemoryStore.items`）；管理面板：直接查 PG | recall 路径不变（进程内存）；**管理面板应改为读 `self.items`**（见下方） | **否，但需修复 list 端点**（见下方分析） |
| `UserPreference` | `PromptAssembler` 每次 run 直查 PG | 直查远端 PG（50ms/run） | **是，建议进程内缓存**（见下方） |
| `Documents` | 直接查 PG（`api/documents.py`） | 不变（远端 PG） | 否（冷路径，面板打开时读） |
| `RagChunk` | Milvus 驱动检索 → PG 取文本 | 不变（远端 PG + Milvus） | 否（query-dependent，命中率低） |
| `MemoryNodes/Edges` | Neo4j 驱动图遍历 → PG 镜像 | 不变（远端 PG + Neo4j） | 否（同上） |

#### LTM recall 路径已缓存（进程内存）

`LongTermMemoryStore`（`backend/app/memory/long_term.py`）采用**混合内存 + PG 持久化**架构：

```
启动时：
  load_from_storage()
    → SELECT * FROM long_term_memory  ← 一次性批量加载到 self.items
    → self.items = [Item(...), Item(...), ...]

Agent run recall：
  recall(query, top_k)
    → 遍历 self.items（进程内存）
    → cosine_similarity(query_emb, item.embedding)  ← 纯内存计算
    → 返回 top_k items
    → 不查 PG！

新增记忆：
  add(content)
    → self.items.append(item)  ← 内存更新
    → INSERT INTO long_term_memory  ← PG 持久化

管理面板（当前）：
  list_ltm_memories()
    → 直接查 PG（分页 + 过滤）  ← 唯一查 PG 的路径

管理面板（改进后）：
  list_ltm_memories()
    → svc.ltm.list_items(user_id, agent_id, category, tag, page, size)
    → 遍历 self.items 做过滤 + 分页  ← 进程内存，0ms
    → MemoryService 未初始化时回退到 PG 查询
```

所以 **LTM 的 recall 路径不经过 PG**，已经是进程内缓存了。双 DB 改造对 LTM recall 性能无影响。

管理面板的 `list_ltm_memories` **当前**直接查 PG，但数据其实已在 `self.items` 中（`recall` / `filter_by_category` / `update_item` / `delete_item` 都已从 `self.items` 读写）。这是一个**架构不一致**：同一份数据，recall 路径走内存，list 路径却绕回 PG。

**改进方案**：给 `LongTerm` 类新增 `list_items(user_id, agent_id, category, tag, page, size) → (items, total)` 方法，从 `self.items` 做过滤 + 分页（与 `recall` 的 `user_id` 过滤方式一致），然后让 API 端点调它。MemoryService 未初始化时回退到当前 PG 查询逻辑。这样管理面板延迟从 50ms（远端 PG）变为 0ms（进程内存），无需引入 Redis 或任何新缓存层。

#### UserPreference 建议新增进程内缓存

`PromptAssembler._build_profile_block`（`prompt_assembler.py` 第 306-314 行）在**每次 agent run** 都直查 PG 读取全部用户偏好：

```python
async with get_db() as session:
    stmt = select(_UP).where(_UP.user_id == user_id)
    rows = (await session.execute(stmt)).scalars().all()
    prefs = {r.key: r.value for r in rows}
```

双 DB 模式下这是每次 run 50ms 远端 PG 读。偏好数据**写频率极低**（用户手动编辑或 LLM 自动提取），**读频率高**（每次 run），结果集小（~10-50 key-value）。是理想的进程内缓存候选：

```python
_pref_cache: dict[str, tuple[dict[str, str], float]] = {}  # user_id → (prefs, expire_ts)

async def _load_preferences(user_id: str) -> dict[str, str]:
    now = time.time()
    cached = _pref_cache.get(user_id)
    if cached and cached[1] > now:
        return cached[0]                                # ← 0ms 进程内命中
    async with get_remote_db() as db:
        rows = (await db.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )).scalars().all()
        prefs = {r.key: r.value for r in rows}
        _pref_cache[user_id] = (prefs, now + 300)       # 5min TTL
        return prefs
```

写入时（用户编辑偏好 / LLM 提取）清缓存：`_pref_cache.pop(user_id, None)`。

#### 总结

| 实体 | 当前缓存方式 | 双 DB 后 | 原因 |
|---|---|---|---|
| Agent | Redis KV (300s) | 直读本地 SQLite（0.1ms） | 表移到 SQLite |
| Workspace | Redis KV (300s) | 直读本地 SQLite（0.1ms） | 表移到 SQLite |
| UserSettings | Redis KV (300s) | 直读远端 PG（50ms）或进程内 dict（0ms） | 表留远端 PG；远端 Redis 无延迟优势 |
| GlobalSettings | Redis KV (300s) | 直读远端 PG（50ms）或进程内 dict（0ms） | 同上 |
| LongTermMemory (recall) | **进程内存**（`self.items`） | 不变（进程内存，不查 PG） | 已是进程内缓存 |
| LongTermMemory (面板) | 无缓存（直查 PG） | **改为读 `self.items`**（进程内存，0ms） | 数据已在内存，list 端点应复用 |
| UserPreference | 无缓存（每次 run 直查 PG） | **建议进程内 dict 缓存**（0ms） | 每次 run 读，写频率极低，理想缓存候选 |
| Documents | 无缓存（直查 PG） | 不变（远端 PG，冷路径） | 面板打开时读，50ms 可接受 |
| RagChunk | 无缓存（Milvus 驱动） | 不变（远端 PG + Milvus） | query-dependent，命中率低 |

### 6.3.1 跨库读路径：`build_adapter_input`

Agent run 启动时，`execute_run` → `build_adapter_input` 的调用链天然跨两个数据库：

```
execute_run(args)
  │
  ├─ get_agent_cached(agent_id)          ← 本地 SQLite（0.1ms）
  ├─ get_workspace_cached(conv_id)      ← 本地 SQLite（0.1ms）
  │
  ├─ get_user_settings(args.user_id)    ← 远端 PG（50ms）！
  │     └─ 读 API Key（agent.api_key 为空时走 UserSettings 四层 key 链）
  │
  └─ build_history_for(agent_id, conv_id)
        ├─ 读 messages / context_summaries  ← 本地 SQLite
        └─ PromptAssembler.assemble()
              ├─ 读 long_term_memory         ← 远端 PG + Milvus
              ├─ 读 user_preferences         ← 远端 PG
              └─ RAG 检索                     ← 远端 PG + Milvus
```

这不是事务问题（均为读操作），但意味着 **run 启动延迟仍含一次 50ms 远端 PG 读**。由于一轮对话只查一次，对用户体验影响可忽略。

**改造要点**：`build_adapter_input` 中 `get_db()` 调用（读 `Conversation` 判断 group chat）改为 `get_local_db()`；`get_user_settings` / `get_app_settings` 内部改为 `get_remote_db()`。两个 session 不需要合并。

### 6.4 各 API 路由改造

| 文件 | 主要操作的表 | 目标 session |
|---|---|---|
| `api/conversations.py` | conversations, messages, agent_runs | `get_local_db()` |
| `api/messages.py` | messages | `get_local_db()` |
| `api/agents.py` | agents | `get_local_db()` ← **改为本地** |
| `api/artifacts.py` | artifacts | `get_local_db()` |
| `api/auth.py` | users | `get_remote_db()` |
| `api/documents.py` | documents, rag_chunks | `get_remote_db()` |
| `api/mcp.py` | mcp_servers | `get_local_db()` ← **改为本地** |
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
| **KV Cache** (Agent) | 缓存 Agent 配置 | **移除** | Agent 在本地 SQLite，读取 0.1ms |
| **KV Cache** (UserSettings) | 缓存 API Key | **移除** | 远端 Redis 缓存命中也 50ms RTT，与直接查 PG 相同 |
| **KV Cache** (Workspace) | 缓存 Workspace | **移除** | Workspace 在本地 SQLite，读取 0.1ms |
| **KV Cache** (GlobalSettings) | 缓存系统配置 | **移除** | 同上，远端 Redis 缓存无延迟优势 |
| **Stream write-behind** | 异步批量写 messages | **移除** | SQLite 直写 0.1ms，不需要缓冲 |
| **Stream crash recovery** | 恢复中断的 streaming 消息 | **简化** | SQLite WAL 模式自带崩溃恢复 |
| ~~SSE pub/sub~~ | ~~全局 SSE 事件广播~~ | **不适用** | 当前 EventBus 已用 `asyncio.Queue`，从未使用 Redis pub/sub（见 §7.4） |
| **Rate limiting** | API 限流 | **可选** | 本地单用户可不需要限流 |

**结论：本地双 DB 模式下，Redis 完全不需要。**

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

> **PG 专有语法处理**：`recovery_scan.py` 中 `_insert_orphaned_message` 使用了 `sqlalchemy.dialects.postgresql.insert`（PG 专有的 `ON CONFLICT DO NOTHING` 语法）。双 DB 模式下这些函数写 SQLite，需要替换为 SQLite 兼容的 `INSERT OR IGNORE`：
>
> ```python
> from sqlalchemy.dialects.sqlite import insert as sqlite_insert
>
> async def _insert_orphaned_message(event_data: dict) -> None:
>     async with get_local_db() as db:
>         await db.execute(
>             sqlite_insert(Message).values(
>                 id=msg_id, ...
>             ).prefix_with("OR IGNORE")
>         )
> ```
>
> 同理，`async_db_writer.py` 中的 `pg_insert` 在双 DB 模式下不执行（消费者不启动），但需确保没有其他路径直接调用 PG 专有语法。已确认：`pg_insert` 仅在 `async_db_writer.py` 和 `recovery_scan.py` 两处使用，均有条件分支保护。

### 7.4 SSE 事件推送（无需改造）

> **勘误**：本文档初版误述为「当前 SSE 依赖 Redis pub/sub」。实际上当前 `EventBus`（`backend/app/services/event_bus.py`）已经是纯进程内 `asyncio.Queue` 架构：
>
> ```python
> class EventBus:
>     def publish(self, event, user_id=None):
>         for sub in self._subscribers:
>             if user_id is None or sub.user_id == user_id:
>                 _offer(sub.queue, event)  # put_nowait
> ```
>
> SSE 端点（`backend/app/api/stream.py`）直接从 `event_bus.subscribe()` 的 queue 取事件。**从未使用 Redis pub/sub**。
>
> 因此双 DB 模式下 SSE 推送**无需任何改造**。

### 7.5 `main.py` 启动流程改造

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

    if redis_client is not None and not is_dual_db:
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

当用户从服务器部署切换到本地双 DB 部署时，需要将 10 张本地表的数据从 PG 导入到本地 SQLite。

**迁移脚本** `scripts/migrate_to_dual_db.py`：

```python
"""将本地表从 PostgreSQL 迁移到本地 SQLite。

用法：
    DATABASE_URL=postgresql+asyncpg://remote-server/agenthub \
    DATABASE_LOCAL_URL=sqlite+aiosqlite:///./local.db \
    python scripts/migrate_to_dual_db.py
"""

LOCAL_TABLES = [
    "messages", "conversations", "agent_runs", "agent_run_checkpoints",
    "artifacts", "workspaces", "attachments", "conversation_context_summaries",
    "agents", "mcp_servers",
]

REMOTE_TABLES = [
    "users", "user_settings", "user_preferences",
    "global_settings", "app_settings",
    "rag_chunks", "long_term_memory", "chat_history",
    "memory_nodes", "memory_edges",
    "documents", "document_versions",
]

async def migrate():
    # 1. 在本地 SQLite 上 create_all（创建 10 张本地表）
    # 2. 遍历 LOCAL_TABLES：
    #    - 从 PG SELECT * 批量读取（分页，每批 1000 行）
    #    - INSERT INTO SQLite（使用 INSERT OR IGNORE 避免重复）
    # 3. 验证行数一致
    # 4. REMOTE_TABLES 不迁移（保留在 PG）
    ...
```

### 8.2 双 DB → 单 DB 回滚

如果需要回滚到单 PG 模式：

1. 设置 `DATABASE_LOCAL_URL=`（空值或不设置）
2. 运行反向迁移脚本：将 SQLite 中的 10 张表数据导回 PG
3. 重启后端

### 8.3 数据一致性

双 DB 模式下，两张 DB 之间**没有事务一致性保证**。但分析各场景：

| 场景 | 风险 | 评估 |
|---|---|---|
| 写 conversation 时远端 User 已被删除 | `user_id` 指向不存在的 User | User 删除是极低频操作，且会级联清理本地数据 |
| 本地 SQLite 损坏 | 消息丢失 | SQLite WAL 模式 + 定期备份（workspace 目录下） |
| 本地 Agent 被删除，远端 LTM 仍有 `agent_id` 引用 | LTM 查询时 Agent 不存在 | `long_term_memory.agent_id` 已是无 FK 纯字符串，App 层处理 None |

### 8.4 `get_db` 别名回退的过渡期语义

双 DB 模式下 `get_db = get_remote_db` 作为向后兼容别名。过渡期未迁移的文件调用 `get_db()` 访问本地表时，行为取决于 PG 中是否仍保留本地表：

| 场景 | PG 保留本地表 | PG 已删本地表 |
|---|---|---|
| 未迁移文件调 `get_db()` 写本地表 | ✅ 写远端 PG，慢但不报错（双写风险） | ❌ 报错 `table not found` |

**过渡期建议**：迁移后**不删** PG 中的本地表，保留为只读影子表。所有 `get_db()` 调用即使未迁移也能工作（写远端 PG 慢，但不出错）。待全部文件迁移完成并通过测试后，再删除 PG 中的本地表。

**风险**：双写期间可能出现数据分叉——同一条 message 被 `get_local_db()` 写入 SQLite、被未迁移的 `get_db()` 写入 PG。由于两者都有 `id` 主键去重，不会产生重复行，但查询时可能读到旧数据。过渡期应尽快完成全部迁移。

---

## 9. 深入：SQLite 的适用性分析

### 9.1 并发写入

SQLite WAL 模式支持 **多读 + 单写** 并发：

- 多个 SSE 连接可以同时读（不阻塞）
- 写操作串行化（通过 `busy_timeout=5000` 等待）
- 单 run 内消息是追加到同一个 parts 数组，无并发写冲突

**并行子任务场景**（Orchestrator DAG 波调度）：当 Orchestrator 派发多个并行子任务时，每个子 run 各自往 `messages` 表 INSERT 不同行：

```
Orchestrator dispatch_plan → 3 个并行子任务
  ├─ subagent-1: consume_stream → persist_event → INSERT/UPDATE messages (SQLite)
  ├─ subagent-2: consume_stream → persist_event → INSERT/UPDATE messages (SQLite)
  └─ subagent-3: consume_stream → persist_event → INSERT/UPDATE messages (SQLite)
                                      ↓
                          SQLite 写锁串行化
                          单次 INSERT ~0.1ms × 3 = ~0.3ms 总计
```

单次 INSERT 0.1ms，即使 5 个并行子任务串行化后总计 ~0.5ms，远低于 `busy_timeout=5000`。但高频 `part.delta` UPDATE 场景下（每个子任务每 token 一次 UPDATE），tail latency 需要基准测试验证。**建议实施时做一次并行 N 子任务的写锁竞争基准测试**。

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

### 9.4 JSON LIKE 搜索跨库一致性

`search_service.py` 使用原始 SQL `LIKE '%' || :q || '%'` 搜 `m.parts`（JSON 列）。PG 的 JSONB 和 SQLite 的 JSON 在文本序列化上有差异：

- **PG JSONB**：存储为紧凑二进制格式，`LIKE` 时序列化为文本（key 顺序可能重排、无多余空格）
- **SQLite JSON**：存储为 TEXT，`LIKE` 直接匹配原始文本（key 顺序为插入顺序）

同一个 message 的 parts 在两个库里可能有不同的文本表示（whitespace、key 顺序、unicode 转义），导致搜索结果不一致。

**影响评估**：迁移后新消息只在 SQLite，搜索一致。但迁移前的旧消息如果同时存在 PG（影子表）和 SQLite 中，两者搜索命中可能不同。由于过渡期 PG 本地表只读，不会产生新数据，影响有限。

**缓解措施**：迁移脚本应确保 JSON 序列化格式统一（如 `json.dumps(obj, ensure_ascii=False, separators=(',', ':'))`），使 SQLite 存储的 JSON 文本与 PG JSONB 序列化后一致。或者迁移后对 SQLite 中的 JSON 列做一次规范化。

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

test_dual_db_agent_local
    → 验证 Agent 从本地 SQLite 读取（不走 Redis 缓存）

test_dual_db_conversation_user_id_no_fk
    → 验证无 FK 约束下 user_id 仍正确隔离

test_dual_db_redis_not_used
    → 验证双 DB 模式下 DBWriterConsumer 不启动、KV Cache 不启用

test_dual_db_recovery_scan_local
    → 验证 SQLite WAL 下的 stuck message 恢复（使用 INSERT OR IGNORE 而非 pg_insert）

test_dual_db_message_agent_fk_intact
    → 验证 messages.agent_id → agents.id FK 约束在 SQLite 内部正常工作

test_dual_db_artifact_agent_fk_intact
    → 验证 artifacts.created_by_agent_id → agents.id FK 约束在 SQLite 内部正常工作

test_dual_db_checkpoint_run_fk_intact
    → 验证 agent_run_checkpoints.run_id → agent_runs.id FK 约束在 SQLite 内部正常工作

test_dual_db_build_adapter_input_cross_db
    → 验证 run 启动时跨库读路径正常（SQLite Agent + PG UserSettings）

test_dual_db_parallel_subagent_write
    → 验证并行子任务 DAG 派发下 SQLite 写锁不超时

test_dual_db_json_like_search_consistency
    → 验证 JSON LIKE 搜索在 SQLite 与 PG 间结果一致

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
| `backend/app/db/models.py` | 移除 3 个跨库 FK 约束（仅 `user_id`） | ~6 行 |
| `backend/app/db/table_routing.py` | 新建：模型 → 引擎路由表 | ~30 行 |
| `backend/app/services/agent_runner.py` | `persist_event` / `_persist_or_stream` 双 DB 分支 | ~50 行 |
| `backend/app/services/async_db_writer.py` | `start_db_writer` 双 DB 模式跳过 | ~5 行 |
| `backend/app/services/recovery_scan.py` | `scan_interrupted_messages` 双 DB 简化 + `pg_insert` → SQLite `INSERT OR IGNORE` | ~25 行 |
| `backend/app/infra/cache_helpers.py` | 简化为本地直读，移除 Redis 缓存逻辑 | ~30 行 |
| `backend/app/main.py` | lifespan 启动逻辑条件分支 + `_seed_guide_agent` 改 `get_local_db` | ~15 行 |
| `backend/.env.example` | 新增 `DATABASE_LOCAL_URL` 注释 | ~12 行 |

### 11.2 需要审查的文件（批量 `get_db()` → `get_local_db()` / `get_remote_db()`）

> **范围修正**：实际 grep 命中 **43 个文件**使用 `get_db() as db`，文档初版仅列出 20 个。以下补全了 `tools/` 层 12 个文件及其他遗漏文件。

#### API 层

| 文件 | 数量 | 目标 |
|---|---|---|
| `api/conversations.py` | ~15 处 | `get_local_db` |
| `api/messages.py` | ~5 处 | `get_local_db` |
| `api/agents.py` | ~10 处 | `get_local_db` ← **改为本地** |
| `api/artifacts.py` | ~5 处 | `get_local_db` |
| `api/auth.py` | ~3 处 | `get_remote_db` |
| `api/documents.py` | ~8 处 | `get_remote_db` |
| `api/mcp.py` | ~3 处 | `get_local_db` ← **改为本地** |
| `api/profile.py` | ~5 处 | `get_remote_db` |
| `api/settings.py` | ~3 处 | `get_remote_db` |
| `api/workspaces.py` | ~3 处 | `get_local_db` |
| `api/memory.py` | ~5 处 | `get_remote_db` |
| `api/runs_misc.py` | ~3 处 | `get_local_db` |
| `api/mobile/routes.py` | ~2 处 | `get_local_db` / `get_remote_db` |
| `api/deployments.py` | ~2 处 | `get_local_db` |
| `api/stream.py` | ~1 处 | `get_remote_db`（SSE 连接时验 User） |

#### Services 层

| 文件 | 数量 | 目标 |
|---|---|---|
| `services/agent_runner.py` | ~10 处 | `get_local_db` |
| `services/agent_loop.py` | ~5 处 | `get_local_db` |
| `services/conversation_service.py` | ~10 处 | `get_local_db` |
| `services/orchestrator.py` | ~8 处 | `get_local_db` |
| `services/tool_executor.py` | ~3 处 | `get_local_db` |
| `services/rag_service.py` | ~5 处 | `get_remote_db` |
| `services/memory_service.py` | ~5 处 | `get_remote_db` |
| `services/settings_service.py` | ~5 处 | `get_remote_db` |
| `services/compact_pipeline.py` | ~5 处 | `get_local_db` |
| `services/document_service.py` | ~5 处 | `get_remote_db` |
| `services/search_service.py` | ~1 处 | `get_local_db` |
| `services/conversation_context.py` | ~3 处 | `get_local_db` |
| `services/context_compaction_service.py` | ~3 处 | `get_local_db` |
| `services/checkpoint_service.py` | ~2 处 | `get_local_db` |
| `services/attachment_service.py` | ~2 处 | `get_local_db` |
| `services/artifact_service.py` | ~3 处 | `get_local_db` |
| `services/global_settings_service.py` | ~2 处 | `get_remote_db` |
| `services/usage_summary_service.py` | ~2 处 | `get_local_db` |
| `services/plan_usage_service.py` | ~2 处 | `get_local_db` |
| `services/deploy_command_service.py` | ~2 处 | `get_local_db` |
| `services/workspace_env_service.py` | ~2 处 | `get_local_db` |
| `services/hooks/tool_approval.py` | ~1 处 | `get_local_db` |
| `services/agent_load_tracker.py` | ~1 处 | `get_local_db` |
| `services/recovery_scan.py` | ~5 处 | `get_local_db` |
| `services/async_db_writer.py` | ~3 处 | `get_local_db`（双 DB 时不启动） |
| `infra/cache_helpers.py` | ~5 处 | `get_local_db` / `get_remote_db` |
| `auth/ownership.py` | ~1 处 | `get_remote_db` |
| `code_intelligence/bootstrap.py` | ~1 处 | `get_local_db` |
| `memory/session_memory.py` | ~2 处 | `get_remote_db` |

#### Tools 层（初版完全遗漏，12 个文件）

| 文件 | 操作的表 | 目标 |
|---|---|---|
| `tools/write_artifact.py` | artifacts | `get_local_db` |
| `tools/update_artifact.py` | artifacts | `get_local_db` |
| `tools/read_artifact.py` | artifacts | `get_local_db` |
| `tools/task_dispatch.py` | messages, agent_runs | `get_local_db` |
| `tools/read_attachment.py` | attachments | `get_local_db` |
| `tools/manage_profile.py` | user_preferences | `get_remote_db` |
| `tools/manage_memory.py` | long_term_memory | `get_remote_db` |
| `tools/manage_mcp.py` | mcp_servers | `get_local_db` |
| `tools/manage_documents.py` | documents, rag_chunks | `get_remote_db` |
| `tools/manage_conversations.py` | conversations | `get_local_db` |
| `tools/manage_agents.py` | agents | `get_local_db` |
| `tools/fs_write.py` | workspaces | `get_local_db` |
| `tools/fs_edit.py` | workspaces | `get_local_db` |
| `tools/deploy_artifact.py` | artifacts | `get_local_db` |

> **注意**：`manage_*` 系列工具可能同时访问本地表和远端表（如 `manage_agents` 读 agents 本地表、`manage_memory` 读 long_term_memory 远端表），需拆分为两个 session。

### 11.3 新建文件

| 文件 | 用途 |
|---|---|
| `backend/app/db/table_routing.py` | 模型分类常量 + 路由辅助函数 |
| `scripts/migrate_to_dual_db.py` | 单 DB → 双 DB 数据迁移脚本 |

---

## 12. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| 跨库 FK 移除导致数据不一致 | 低 | 中 | App 层已有 user_id JWT 隔离校验；User 删除时级联清理本地数据 |
| SQLite 并发写锁竞争 | 低 | 低 | WAL 模式 + busy_timeout=5000；单用户场景无高并发 |
| 本地 SQLite 文件损坏 | 极低 | 高 | WAL 模式保证持久性；定期备份到 workspace 目录 |
| 服务函数遗漏改造（仍用 `get_db`） | 中 | 中 | `get_db` 别名指向 `get_remote_db`；过渡期 PG 保留本地表影子表，未迁移的调用写远端 PG 不报错（见 §8.4） |
| 双 DB 事务边界问题 | 低 | 中 | 当前代码中不存在跨表事务（每个 `get_db()` 块只操作同类表） |
| 迁移脚本数据丢失 | 中 | 高 | 分页迁移 + 行数校验 + 回滚脚本 |
| Builtin Agent 种子在每台设备重复创建 | 低 | 低 | 种子逻辑已幂等（`INSERT OR IGNORE`），重复启动不报错 |
| 本地 Agent 编辑后远端 LTM 引用悬空 | 低 | 低 | `long_term_memory.agent_id` 已是无 FK 纯字符串，App 层处理 None |

---

## 13. 实施计划

### Phase 1：基础设施（1-2 天）

- [x] 新建 `table_routing.py` 模型分类常量（10 张本地 + 12 张远端）
- [x] 改造 `engine.py` 支持双引擎初始化
- [x] 改造 `config.py` 新增 `database_local_url`
- [x] 更新 `.env.example`
- [x] 改造 `models.py` 移除 3 个跨库 FK（仅 `user_id`）
- [x] 编写双 DB 模式单元测试

### Phase 2：热路径改造（1-2 天）

- [x] 改造 `persist_event` / `_persist_or_stream` 双 DB 分支
- [x] 改造 `async_db_writer.py` 条件跳过
- [x] 改造 `recovery_scan.py` 本地模式简化
- [x] 改造 `main.py` lifespan 启动逻辑
- [x] 改造 `cache_helpers.py` 简化为本地直读

### Phase 3：服务层批量改造（3-4 天）

- [x] 逐文件审查 `get_db()` 调用，改为 `get_local_db()` / `get_remote_db()`
- [x] 优先改造热路径文件（`agent_runner`、`conversation_service`、`agent_loop`）
- [x] 改造 `api/agents.py` 和 `api/mcp.py`（从 `get_remote_db` → `get_local_db`）
- [x] 改造 `tools/` 层 12 个文件（初版遗漏）
- [x] 改造 `recovery_scan.py` PG 专有语法（`pg_insert` → `INSERT OR IGNORE`）
- [x] 冷路径文件可后续渐进式改造（`get_db` 别名保证不报错，过渡期 PG 保留本地表影子表）

### Phase 4：迁移与测试（2-3 天）

- [x] 编写 `migrate_to_dual_db.py` 迁移脚本（含 JSON 序列化格式统一）
- [x] 端到端测试：双 DB 模式下完整 Agent 运行
- [x] 性能基准测试：对比单 DB vs 双 DB 的 per-token 延迟
- [x] 并行子任务写锁竞争基准测试（§9.1）
- [x] 回归测试：单 DB 模式向后兼容

### Phase 5：文档同步（0.5 天）

- [x] 更新 `specs/08-db-schema.md`
- [x] 更新 `openspec/specs/persistence/spec.md`
- [x] 更新 `CLAUDE.md` §3.1 五层分层说明
- [x] 更新 `backend/.env.example`

---

## 14. 开放问题（待讨论）

1. **本地 SQLite 备份策略**：是否需要自动备份？频率？是否跟随 workspace 目录一起同步？
2. **多设备同步**：用户在两台电脑上使用时，本地 SQLite（对话数据 + Agent 配置）如何同步？（可考虑 workspace 级别的文件同步）
3. **桌面端集成**：Electron 打包时 SQLite 文件路径如何管理？是否放在 `app.getPath('userData')` 下？
4. **服务器多用户**：服务器部署模式下，是否也考虑给每个用户一个独立的本地 SQLite？（可能过度设计，PG 连接池已经够用）
5. **Agent 配置设备隔离**：Agent 在本地 SQLite，用户在设备 A 上创建的 Agent 不会出现在设备 B 上。这是否是期望行为？还是需要某种同步机制？

> **已解答的问题**（从初版移除）：
> - ~~Builtin Agent 种子~~：`_seed_guide_agent()` 使用 `get_db()`，双 DB 模式改为 `get_local_db()` 即可。种子逻辑已幂等，重复启动不报错。
> - ~~pg_insert 兼容性~~：已确认 `pg_insert` 仅在 `async_db_writer.py` 和 `recovery_scan.py` 两处使用。双 DB 模式下 `recovery_scan` 需替换为 SQLite 的 `INSERT OR IGNORE`（见 §7.3）。
> - ~~UserSettings 跨设备共享~~：UserSettings 在远端 PG，一轮对话只查一次（~50ms），可接受（见 §1.3 延迟预期）。

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

---

## 附录 C：修订记录

| 日期 | 修订内容 |
|---|---|
| 2026-07-22 | 初版：8 张本地 + 13 张远端 |
| 2026-07-22 | 修正：Agent / McpServer 从远端 PG 移到本地 SQLite；Redis KV 缓存彻底不需要；本地表从 8 张增至 10 张，远端表从 13 张减至 12 张。 |
| 2026-07-22 | 勘误与补充：1) 承认 `optimize-stream-persistence` 已解决 SSE 阻塞，动机调整为 Redis 远端 XADD 延迟 + 数据隐私；2) 修正 SSE 已用 asyncio.Queue，从未用 Redis pub/sub；3) 补全 SQLite 内部 FK（`artifacts.created_by_agent_id`、`agent_run_checkpoints.run_id`）和 PG 内部 FK；4) 补充 `build_adapter_input` 跨库读路径说明；5) 补充 `recovery_scan` PG 专有语法 `INSERT OR IGNORE` 方案；6) 补充 `_migrate_columns` PG 专有语法分库执行方案；7) 改造范围从 20 文件修正为 43 文件（补全 `tools/` 层 12 个文件）；8) 补充并行子任务 DAG 波调度写锁分析；9) 补充 `get_db` 别名回退过渡期语义（§8.4）；10) 新增 JSON LIKE 搜索跨库一致性说明（§9.4）；11) 隐私升为一等目标；12) 开放问题从 8 个精简为 5 个。 |
| 2026-07-22 | 补充 §6.3 缓存分类：修正"所有 cache_helpers 都简化为本地直读"的误导性表述——UserSettings/GlobalSettings 留在远端 PG，不能简化为本地直读；明确 LTM/Preferences/Documents 从未被 Redis 缓存（直查 PG）；提供进程内 dict 缓存方案 B 作为 UserSettings 零延迟替代。 |
| 2026-07-22 | 深入分析 §6.3 缓存策略：发现 LTM recall 路径已是进程内存缓存（`LongTermMemoryStore.items`），不经过 PG；UserPreference 每次 run 直查 PG 是理想进程内缓存候选；Documents/RagChunk 为冷路径或 query-dependent，不需要缓存。总结表更新。 |
| 2026-07-22 | 修正 §6.3 LTM 管理面板缓存策略：发现 `list_ltm_memories` 端点绕过 `LongTermMemoryStore.items` 直接查 PG，与 `recall`/`update_item`/`delete_item` 路径不一致；提出改进方案——新增 `LongTerm.list_items()` 方法从 `self.items` 做过滤 + 分页，list 端点改为调它（0ms 进程内存），MemoryService 未初始化时回退 PG。无需 Redis。 |
| 2026-07-22 | **状态变更为「已实施」**。全部 44 个任务完成：双引擎初始化、表路由、persist_event 直写 SQLite、Redis 代码移除、43 个文件 get_db→get_local_db/get_remote_db 迁移、跨库 FK 移除、进程内 dict TTL 缓存、LTM 管理面板修复、迁移脚本、29 个测试全部通过。 |
