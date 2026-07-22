# Add Dual-Database Local Mode

## Why

AChat 当前的持久化架构依赖单一 PostgreSQL + 可选 Redis 缓冲层。在「基础设施远端部署 + 用户本地跑前后端」场景下，Redis 和 PG 都在远端，Redis KV 缓存命中与直接查 PG 延迟相同（50ms RTT），Redis Stream write-behind 的 XADD 也是 50ms——Redis 成了无用的间接层。更根本的问题是：用户对话数据（messages、agents 配置等）存储在远端 PG，无法满足数据隐私需求。

双 DB 方案将对话热数据和个人配置放本地 SQLite（0.1ms RTT，直写），用户系统和知识/RAG 数据留远端 PG（50ms 可接受），彻底移除 Redis 依赖。per-token 消息持久化延迟从 50ms 降至 0.1ms（500 倍提升），且对话数据不出本机。

## What Changes

- **BREAKING**: 移除全部 Redis 代码——KV 缓存（`cache_helpers.py` 4 个函数）、Stream write-behind（`async_db_writer.py` `DBWriterConsumer`）、Redis 基础设施集成（`infra/factory.py` 中 Redis client 构建逻辑）、`recovery_scan.py` 的 Redis Stream 回放路径
- **BREAKING**: 新增本地 SQLite 引擎，承载 10 张对话热数据 + 个人配置表（`messages`、`conversations`、`agent_runs`、`agent_run_checkpoints`、`artifacts`、`workspaces`、`attachments`、`conversation_context_summaries`、`agents`、`mcp_servers`）
- 远端 PostgreSQL 保留 12 张用户系统 + 知识/RAG 表（`users`、`user_settings`、`user_preferences`、`global_settings`、`app_settings`、`rag_chunks`、`long_term_memory`、`chat_history`、`memory_nodes`、`memory_edges`、`documents`、`document_versions`）
- 新建 `backend/app/db/table_routing.py`：模型分类常量 + 路由辅助函数
- 新增 `get_local_db()` / `get_remote_db()` session 获取函数；`get_db` 保留为 `get_remote_db` 别名用于过渡期
- 移除 `models.py` 中 3 个跨库 FK 约束（`conversations.user_id`、`agents.user_id`、`mcp_servers.user_id` → `users.id`），改为纯 String 列
- `persist_event` 改为直接写本地 SQLite（0.1ms），不再走 Redis Stream
- `cache_helpers.py` 简化：Agent/Workspace 改为本地 SQLite 直读；UserSettings/GlobalSettings 改为远端 PG 直读 + 进程内 dict TTL 缓存
- `recovery_scan.py` 简化：SQLite WAL 自带崩溃恢复，仅扫描 stuck `streaming` 消息标记为 `interrupted`，移除 Redis Stream 回放
- `main.py` lifespan 移除 Redis 初始化和 `DBWriterConsumer` 启动
- 新增 `scripts/migrate_to_dual_db.py` 迁移脚本：单 DB → 双 DB 数据导入
- 43 个文件批量将 `get_db()` 替换为 `get_local_db()` / `get_remote_db()`
- `UserPreference` 新增进程内 dict 缓存（每次 agent run 读，写频率极低）
- `LongTermMemory` 管理面板端点改为读 `self.items` 进程内存（与 recall 路径一致）
- `_migrate_columns` 拆分为 PG 版和 SQLite 版，按引擎分别执行

## Capabilities

### New Capabilities

_(无)_

### Modified Capabilities

- `persistence`: 持久化架构从单 PG + Redis 缓冲层变更为双 DB（SQLite + PG）直写模式。新增表路由、双引擎初始化、跨库 FK 移除、进程内缓存替代 Redis KV 缓存等需求。移除 Redis Stream write-behind 和 Redis KV 缓存相关需求。

## Impact

- **DB Engine**: `backend/app/db/engine.py` — 单引擎 → 双引擎，新增 `get_local_db()` / `get_remote_db()`
- **Config**: `backend/app/config.py` — 新增 `database_local_url` 配置项
- **Table Routing**: `backend/app/db/table_routing.py` — 新建文件
- **Models**: `backend/app/db/models.py` — 移除 3 个跨库 FK
- **Agent Runner**: `backend/app/services/agent_runner.py` — `persist_event` / `_persist_or_stream` 改为直写本地 SQLite
- **Cache Helpers**: `backend/app/infra/cache_helpers.py` — 移除 Redis KV 缓存，改为本地直读 / 进程内 dict
- **Async DB Writer**: `backend/app/services/async_db_writer.py` — 移除 Redis Stream 消费者
- **Recovery Scan**: `backend/app/services/recovery_scan.py` — 简化为 SQLite WAL 崩溃恢复
- **Main**: `backend/app/main.py` — lifespan 移除 Redis 初始化和 DBWriterConsumer
- **Infra Factory**: `backend/app/infra/factory.py` — 移除 Redis client 构建
- **Batch Migration**: 43 个文件 `get_db()` → `get_local_db()` / `get_remote_db()`（API 层 13 文件 + Services 层 20 文件 + Tools 层 12 文件 + 其他）
- **Migration Script**: `scripts/migrate_to_dual_db.py` — 新建
- **Env**: `backend/.env.example` — 新增 `DATABASE_LOCAL_URL`
- **No new dependencies**: `aiosqlite` 已在项目中使用（测试环境）
- **No StreamEvent contract changes**: 事件类型和字段不变，仅持久化策略变更
- **No frontend changes**: 前端不感知持久化层变更
