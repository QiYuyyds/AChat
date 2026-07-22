# Design: Add Dual-Database Local Mode

## Context

AChat 当前使用单 PG + 可选 Redis 的持久化架构。`redis-async-persistence` 变更引入了 Redis KV 缓存（Agent/UserSettings/Workspace/GlobalSettings）和 Redis Stream write-behind（part.delta/message.start/end 异步批量写 PG）。`optimize-stream-persistence` 变更将 `publish()` 提前到 `persist_event()` 之前，并扩展 Stream 覆盖到 message.start/end。

当前状态：
- SSE 推送不阻塞（EventBus 用 asyncio.Queue，从未用 Redis pub/sub）
- Redis 可用时：事件走 XADD → DBWriterConsumer 批量 flush PG；Redis 远端时 XADD 仍 50ms RTT
- Redis KV 缓存：Agent/Workspace/UserSettings/GlobalSettings 四个实体，远端 Redis 命中也 50ms RTT
- `recovery_scan.py` 从 Redis Stream 回放重建中断的 streaming 消息
- `cache_helpers.py` 4 个 Redis read-through 缓存函数
- `async_db_writer.py` DBWriterConsumer 消费 Redis Stream 批量写 PG

**关键洞察**：当 Redis 和 PG 都在远端时，Redis 缓存命中（50ms RTT）不比直接查 PG 快。Redis 成了无用的间接层。本地 SQLite（0.1ms RTT）直写直读足够快，完全不需要 Redis。

## Goals / Non-Goals

**Goals:**

- 对话热数据（messages、agent_runs 等）放本地 SQLite，per-token 持久化延迟 < 1ms
- 个人配置（agents、mcp_servers）放本地 SQLite，数据隐私——对话数据不出本机
- 用户系统（users、user_settings）和知识/RAG 数据（rag_chunks、ltm 等）留远端 PG，跨设备一致
- 彻底移除 Redis 依赖——KV 缓存、Stream write-behind、infra 集成全部删除
- 服务器部署模式（单 PG，无 SQLite）行为不变——不设 `DATABASE_LOCAL_URL` 即回退单引擎
- 进程内 dict 缓存替代 Redis KV 缓存，覆盖远端冷数据（UserSettings、GlobalSettings、UserPreference）

**Non-Goals:**

- 不改变 StreamEvent 事件类型契约（事件 schema 不变）
- 不改变前端 store reducer 逻辑（前端不感知持久化层变更）
- 不处理多设备同步（本地 SQLite 的对话数据/Agent 配置如何跨设备同步留待后续变更）
- 不处理桌面端 Electron SQLite 路径管理（留待 desktop-electron 变更）
- 不处理服务器多用户 SQLite 分配（PG 连接池已够用，避免过度设计）
- 不处理本地 SQLite 自动备份策略（留待后续变更）

## Decisions

### D1: 表分类——10 张本地 + 12 张远端

**选择**: 将 22 张表按数据归属和依赖关系分为两组。

本地 SQLite（10 张）——对话热数据 + 个人本地配置：
`messages`、`conversations`、`agent_runs`、`agent_run_checkpoints`、`artifacts`、`workspaces`、`attachments`、`conversation_context_summaries`、`agents`、`mcp_servers`

远端 PostgreSQL（12 张）——用户系统 + 知识/RAG 数据：
`users`、`user_settings`、`user_preferences`、`global_settings`、`app_settings`、`rag_chunks`、`long_term_memory`、`chat_history`、`memory_nodes`、`memory_edges`、`documents`、`document_versions`

**理由**:
- 对话数据是 per-token 热写入，本地 SQLite 直写 0.1ms vs 远端 PG 50ms = 500 倍提升
- Agent 和 McpServer 是用户自己创建/配置的个人配置，不需要放远端统一管理
- 用户系统（认证、API Key）需跨设备共享，留远端 PG
- 依赖 Milvus/ES/Neo4j 的表需与基础设施同机房，留远端 PG
- `messages.agent_id` 和 `agent_runs.agent_id` 原来跨库（Agent 在 PG），Agent 移到 SQLite 后变同库 FK，**可以保留**——这是重要的架构优势

**替代方案**:
- 全部留 PG，本地部署 Redis 把 XADD 降到 0.1ms：纯性能可行且零代码改动，但对话数据仍存远端，不满足隐私需求
- 全部移到本地 SQLite（纯本地模式）：理想态，但 RAG/Milvus/Neo4j 依赖远端 PG 数据，无法全部本地化

### D2: 移除 3 个跨库 FK 约束

**选择**: 移除 `conversations.user_id`、`agents.user_id`、`mcp_servers.user_id` 对 `users.id` 的 FK 约束，改为纯 String 列。

**理由**:
- 只有 3 个跨库 FK，模式统一（全部 `user_id → users.id`）
- `user_id` 来自 JWT 认证上下文，不是用户请求体传入的，不存在伪造
- 所有查询都带 `WHERE user_id = ?` 过滤，App 层做数据隔离
- User 删除是极低频操作，删除时会级联清理本地数据
- SQLite 内部 FK（如 `messages.agent_id → agents.id`、`artifacts.created_by_agent_id → agents.id`）**保留不动**——Agent 和 Message 都在 SQLite，同库 relationship 正常工作

**替代方案**:
- 保留 FK 约束并用触发器做跨库校验：SQLite 不支持跨库 FK，且复杂度不值得
- 完全移除所有 user_id 列：不行，数据隔离依赖此列

### D3: 彻底移除 Redis

**选择**: 删除全部 Redis 相关代码——KV 缓存、Stream write-behind、infra 集成、recovery Stream 回放。

**理由**:
- Redis KV 缓存：Agent/Workspace 移到本地 SQLite 后直读 0.1ms；UserSettings/GlobalSettings 留远端 PG，远端 Redis 命中也 50ms RTT = 无用
- Redis Stream write-behind：本地 SQLite 直写 0.1ms，不需要缓冲层
- Redis Stream crash recovery：SQLite WAL 模式自带崩溃恢复
- SSE 推送：EventBus 已用 asyncio.Queue，从未用 Redis pub/sub
- 本地部署场景完全不需要 Redis，免装

**替代方案**:
- Redis 作为可选降级保留：增加维护复杂度，且本地场景永远不用

### D4: 进程内 dict 缓存替代 Redis KV 缓存

**选择**: 对远端 PG 冷数据（UserSettings、GlobalSettings、UserPreference）引入进程内 dict TTL 缓存。

- `UserSettings`：`build_adapter_input`（run 启动读 API Key）和 `api/settings.py`（前端面板）调用，冷路径，进程内 dict 5min TTL
- `GlobalSettings`：单行表，进程内 dict 5min TTL
- `UserPreference`：`PromptAssembler` 每次 run 读，写频率极低，进程内 dict 5min TTL
- `Agent`/`Workspace`：本地 SQLite 直读 0.1ms，不需要缓存
- `LongTermMemory`：recall 路径已在进程内存（`self.items`），管理面板端点改为读 `self.items`

写入时（用户编辑偏好 / LLM 提取）清缓存：`_process_cache.pop(key, None)`。

**理由**:
- 这三个实体写频率极低、读频率高、结果集小，是理想的进程内缓存候选
- 进程内缓存 0ms 命中，比远端 Redis 50ms 快
- 单 worker 场景（本地部署）不存在缓存一致性问题

**替代方案**:
- 不缓存，直接查远端 PG：UserSettings 一轮对话一次 50ms 可接受；UserPreference 每次 run 50ms 偏高

### D5: SQLite WAL 模式 + busy_timeout

**选择**: SQLite 启用 WAL 模式 + `PRAGMA foreign_keys=ON` + `PRAGMA busy_timeout=5000`。

**理由**:
- WAL 模式：读不阻塞写，写不阻塞读；崩溃恢复已提交事务自动重放
- `foreign_keys=ON`：确保 CASCADE 删除生效
- `busy_timeout=5000`：写锁竞争时等待 5 秒而非立即报错
- 单用户场景无高并发，即使 Orchestrator DAG 并行子任务也只串行化几毫秒

**替代方案**:
- DELETE journal mode：不支持并发读写，不适合流式写入

### D6: get_db 别名指向 get_remote_db 用于过渡期

**选择**: 保留 `get_db = get_remote_db` 作为向后兼容别名。43 个文件中尚未迁移的调用 `get_db()` 时会写到远端 PG（过渡期 PG 保留本地表影子表），慢但不报错。

**理由**:
- 43 文件的批量迁移不可能一次完成，别名保证过渡期不 break
- `get_local_db()` 在单引擎模式（未设 `DATABASE_LOCAL_URL`）时自动回退到 remote session
- 所有文件迁移完成后可清理别名

**替代方案**:
- 一次性替换所有 `get_db()` 调用：风险高，容易遗漏

### D7: _migrate_columns 拆分为 PG 版和 SQLite 版

**选择**: 将 `_migrate_columns` 拆分为 `_migrate_columns_pg`（使用 PG 专有语法 `ADD COLUMN IF NOT EXISTS` 和 `::jsonb`）和 `_migrate_columns_sqlite`（SQLite 不支持 `IF NOT EXISTS` 和 `::jsonb`，靠 try/except 包裹 ALTER TABLE）。

**理由**:
- 当前 `_migrate_columns` 使用 PG 专有语法，SQLite 会报错
- `create_all` 只创建缺失的表，不修改已有表的新列——旧表新增列靠迁移语句
- SQLite 的 `create_all` 已为新表创建全部列，旧表新增列靠 try/except ALTER TABLE

## Risks / Trade-offs

### [R1: SQLite 并发写锁竞争]

Orchestrator DAG 波调度并行子任务时，多个 subagent 同时往 `messages` 表 INSERT/UPDATE 不同行。SQLite WAL 模式写操作串行化，单次 INSERT ~0.1ms，5 个并行子任务串行化后总计 ~0.5ms，远低于 `busy_timeout=5000`。

**缓解**: 实施时做一次并行 N 子任务写锁竞争基准测试。高频 `part.delta` UPDATE 的 tail latency 需验证。

### [R2: 本地 SQLite 文件损坏]

SQLite WAL 模式保证已提交事务的持久性，但文件系统级损坏（磁盘故障、意外断电）可能导致数据丢失。

**缓解**: WAL 模式已提供崩溃恢复；定期备份到 workspace 目录（留待后续变更实现）。

### [R3: 过渡期双写风险]

`get_db` 别名指向 `get_remote_db`，未迁移的文件调 `get_db()` 写本地表时会写到远端 PG 的影子表，而非本地 SQLite。可能导致同一条 message 被写两次（SQLite + PG）。

**缓解**: 过渡期尽快完成全部 43 文件迁移。主键 `id` 去重避免重复行，但查询可能读到旧数据。迁移完成后删除 PG 中的本地表。

### [R4: JSON LIKE 搜索跨库一致性]

`search_service.py` 使用 `LIKE '%' || :q || '%'` 搜 `m.parts`（JSON 列）。PG JSONB 和 SQLite JSON 文本序列化有差异（key 顺序、whitespace），搜索结果可能不一致。

**缓解**: 迁移脚本确保 JSON 序列化格式统一（`json.dumps(obj, ensure_ascii=False, separators=(',', ':'))`）。迁移后新消息只在 SQLite，一致性自然保证。过渡期 PG 影子表只读，影响有限。

### [R5: Agent 设备隔离]

Agent 在本地 SQLite，用户在设备 A 上创建的 Agent 不会出现在设备 B 上。

**缓解**: 这是当前设计的预期行为（本地个人配置）。如果需要跨设备同步，留待后续变更。

## Migration Plan

### Phase 1: 基础设施（1-2 天）

1. 新建 `table_routing.py` 模型分类常量（10 张本地 + 12 张远端）
2. 改造 `engine.py` 支持双引擎初始化 + `get_local_db()` / `get_remote_db()`
3. 改造 `config.py` 新增 `database_local_url`
4. 更新 `.env.example`

### Phase 2: 热路径改造（1-2 天）

1. 改造 `persist_event` / `_persist_or_stream` 直写本地 SQLite
2. 改造 `cache_helpers.py` 移除 Redis KV，改为本地直读 / 进程内 dict
3. 改造 `recovery_scan.py` 简化为 SQLite WAL 崩溃恢复
4. 改造 `main.py` lifespan 移除 Redis 初始化和 DBWriterConsumer

### Phase 3: Redis 代码移除（0.5 天）

1. 删除 `async_db_writer.py` 中 DBWriterConsumer
2. 移除 `infra/factory.py` 中 Redis client 构建逻辑
3. 移除 `infra/cache.py` 中 Redis 缓存实现
4. 清理 `config.py` 中 `redis_url` 配置项
5. 清理 `docker-compose.yml` 中 Redis 服务
6. 清理 `pyproject.toml` 中 `redis[hiredis]` 依赖

### Phase 4: 服务层批量改造（3-4 天）

1. 逐文件审查 `get_db()` 调用，改为 `get_local_db()` / `get_remote_db()`
2. 优先改造热路径文件（`agent_runner`、`conversation_service`、`agent_loop`）
3. 改造 `api/agents.py` 和 `api/mcp.py`（从 `get_remote_db` → `get_local_db`）
4. 改造 `tools/` 层 12 个文件
5. 改造 `recovery_scan.py` PG 专有语法（`pg_insert` → `INSERT OR IGNORE`）
6. 新增 `UserPreference` 进程内缓存
7. 修复 `LongTermMemory` 管理面板端点读 `self.items`

### Phase 5: 迁移与测试（2-3 天）

1. 编写 `migrate_to_dual_db.py` 迁移脚本（含 JSON 序列化格式统一）
2. 端到端测试：双 DB 模式下完整 Agent 运行
3. 性能基准测试：对比单 DB vs 双 DB 的 per-token 延迟
4. 并行子任务写锁竞争基准测试
5. 回归测试：单 DB 模式向后兼容

### Phase 6: 文档同步（0.5 天）

1. 更新 `specs/08-db-schema.md`
2. 更新 `openspec/specs/persistence/spec.md`
3. 更新 `CLAUDE.md` §3.1 五层分层说明
4. 更新 `backend/.env.example`

**回滚策略**: Redis 版本已保留在独立分支。如需回滚，切回 Redis 分支即可。本分支内可通过不设 `DATABASE_LOCAL_URL` 回退到单 PG 模式（但 Redis 代码已移除，单 PG 模式为同步直写）。

## Open Questions

1. **本地 SQLite 备份策略**：是否需要自动备份？频率？是否跟随 workspace 目录一起同步？（留待后续变更）
2. **多设备同步**：用户在两台电脑上使用时，本地 SQLite 如何同步？（留待后续变更）
3. **桌面端集成**：Electron 打包时 SQLite 文件路径如何管理？（留待 desktop-electron 变更）
4. **服务器多用户**：服务器部署模式下是否给每个用户独立 SQLite？（可能过度设计，PG 连接池已够用）
5. **Agent 配置设备隔离**：Agent 在本地 SQLite 不跨设备出现，是否需要同步机制？（当前预期行为是设备私有）
