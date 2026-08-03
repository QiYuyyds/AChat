## 1. 基础设施：双引擎 + 表路由 + 配置

- [x] 1.1 新建 `backend/app/db/table_routing.py`，定义 `LOCAL_TABLES`（10 张）和 `REMOTE_TABLES`（12 张）常量集合，以及 `get_local_table_objects()` / `get_remote_table_objects()` 返回对应 SQLAlchemy `Table` 对象
- [x] 1.2 改造 `backend/app/config.py`，新增 `database_local_url: str | None = None` 配置项
- [x] 1.3 改造 `backend/app/db/engine.py`，实现双引擎初始化：`_local_engine` / `_local_session_factory`（SQLite，仅当 `database_local_url` 设置时）+ `_remote_engine` / `_remote_session_factory`（PG，始终初始化），分别 `create_all` 本地表和远端表
- [x] 1.4 在 `engine.py` 新增 `get_local_db()` / `get_remote_db()` session 获取函数；`get_db` 设为 `get_remote_db` 别名（过渡期兼容）
- [x] 1.5 拆分 `_migrate_columns` 为 `_migrate_columns_pg`（PG 专有语法 `ADD COLUMN IF NOT EXISTS` + `::jsonb`）和 `_migrate_columns_sqlite`（try/except 包裹 `ALTER TABLE`），`init_db` 按引擎分别调用
- [x] 1.6 更新 `backend/.env.example`，新增 `DATABASE_LOCAL_URL` 配置项及注释说明

## 2. 热路径改造：persist_event + 缓存 + 恢复 + 启动

- [x] 2.1 改造 `backend/app/services/agent_runner.py` 中 `persist_event`：移除 Redis Stream / XADD 分支，message.start/part.delta/message.end 全部直写 `get_local_db()`（双 DB 模式）或同步写 PG（服务器模式）
- [x] 2.2 改造 `agent_runner.py` 中 `_persist_or_stream`：移除 Redis XADD 逻辑，改为直写本地 SQLite UPDATE parts
- [x] 2.3 改造 `agent_runner.py` 中 usage 事件路径：保持 fire-and-forget `asyncio.create_task`，但写入目标改为 `get_local_db()`
- [x] 2.4 改造 `backend/app/infra/cache_helpers.py`：`get_agent_cached` / `get_workspace_cached` 改为本地 SQLite 直读（`get_local_db`）；`get_user_settings_cached` / `get_global_settings_cached` 改为远端 PG 直读（`get_remote_db`）+ 进程内 dict TTL 缓存（5min）
- [x] 2.5 改造 `backend/app/services/recovery_scan.py`：移除 Redis Stream 回放路径，简化为扫描 stuck `streaming` 消息标记 `interrupted`；`pg_insert` 替换为 SQLite `INSERT OR IGNORE`
- [x] 2.6 改造 `backend/app/main.py` lifespan：移除 Redis 初始化和 `start_db_writer` / `stop_db_writer` 调用
- [x] 2.7 改造 `main.py` 中 `_seed_guide_agent` 使用 `get_local_db()`

## 3. Redis 代码移除

- [x] 3.1 删除 `backend/app/services/async_db_writer.py` 中 `DBWriterConsumer` 类和 `start_db_writer` / `stop_db_writer` 函数（或清空文件保留导入兼容）
- [x] 3.2 移除 `backend/app/infra/factory.py` 中 Redis client 构建逻辑和 `redis_client` 字段
- [x] 3.3 移除 `backend/app/infra/cache.py` 中 Redis 缓存实现，保留 no-op fallback 或删除文件
- [x] 3.4 清理 `backend/app/config.py` 中 `redis_url` 配置项
- [x] 3.5 清理 `docker-compose.yml` / `docker-compose*.yml` 中 Redis 服务定义
- [x] 3.6 清理 `pyproject.toml` 中 `redis[hiredis]` 依赖
- [x] 3.7 全局搜索并清理代码中所有 `redis_client` / `_get_redis_client` / `xadd_event` / `use_stream` 引用

## 4. 服务层批量改造：get_db() → get_local_db() / get_remote_db()

- [x] 4.1 改造 API 层本地表文件（`get_db` → `get_local_db`）：`api/conversations.py`、`api/messages.py`、`api/agents.py`、`api/artifacts.py`、`api/mcp.py`、`api/workspaces.py`、`api/runs_misc.py`、`api/deployments.py`
- [x] 4.2 改造 API 层远端表文件（`get_db` → `get_remote_db`）：`api/auth.py`、`api/documents.py`、`api/profile.py`、`api/settings.py`、`api/memory.py`、`api/mobile/routes.py`、`api/stream.py`
- [x] 4.3 改造 Services 层本地表文件（`get_db` → `get_local_db`）：`services/agent_runner.py`、`services/agent_loop.py`、`services/conversation_service.py`、`services/orchestrator.py`、`services/tool_executor.py`、`services/compact_pipeline.py`、`services/conversation_context.py`、`services/context_compaction_service.py`、`services/checkpoint_service.py`、`services/attachment_service.py`、`services/artifact_service.py`、`services/search_service.py`、`services/usage_summary_service.py`、`services/plan_usage_service.py`、`services/deploy_command_service.py`、`services/workspace_env_service.py`、`services/hooks/tool_approval.py`、`services/agent_load_tracker.py`、`services/recovery_scan.py`
- [x] 4.4 改造 Services 层远端表文件（`get_db` → `get_remote_db`）：`services/rag_service.py`、`services/memory_service.py`、`services/settings_service.py`、`services/document_service.py`、`services/global_settings_service.py`
- [x] 4.5 改造 Tools 层 12 个文件：`tools/write_artifact.py`、`tools/update_artifact.py`、`tools/read_artifact.py`、`tools/task_dispatch.py`、`tools/read_attachment.py`、`tools/manage_profile.py`、`tools/manage_memory.py`、`tools/manage_mcp.py`、`tools/manage_documents.py`、`tools/manage_conversations.py`、`tools/manage_agents.py`、`tools/fs_write.py`、`tools/fs_edit.py`、`tools/deploy_artifact.py`
- [x] 4.6 改造其他文件：`infra/cache_helpers.py`、`auth/ownership.py`、`code_intelligence/bootstrap.py`、`memory/session_memory.py`
- [x] 4.7 改造 `backend/app/db/models.py`：移除 3 个跨库 FK 约束（`conversations.user_id`、`agents.user_id`、`mcp_servers.user_id` 的 `ForeignKey("users.id")`），改为纯 String 列
- [x] 4.8 新增 `UserPreference` 进程内 dict TTL 缓存（5min），在 `PromptAssembler._build_profile_block` 或独立模块中实现，写入时清缓存
- [x] 4.9 修复 `LongTermMemory` 管理面板端点：新增 `LongTerm.list_items()` 方法从 `self.items` 做过滤 + 分页，`list_ltm_memories` 端点改为调用它，MemoryService 未初始化时回退 PG 查询

## 5. 迁移脚本与测试

- [x] 5.1 编写 `scripts/migrate_to_dual_db.py` 迁移脚本：从 PG 读取 10 张本地表数据，分页批量导入 SQLite（`INSERT OR IGNORE`），含 JSON 序列化格式统一（`json.dumps(obj, ensure_ascii=False, separators=(',', ':'))`）和行数校验
- [x] 5.2 编写双 DB 模式单元测试：双引擎初始化、跨库读路径（SQLite Agent + PG UserSettings）、SQLite 内部 FK 正常工作、Redis 不启动、`get_db` 别名回退
- [x] 5.3 编写测试：`persist_event` 双 DB 模式直写 SQLite（不走 Redis Stream）、usage fire-and-forget 写本地
- [x] 5.4 编写测试：`recovery_scan` SQLite WAL 崩溃恢复（扫描 stuck streaming → interrupted，使用 `INSERT OR IGNORE`）
- [x] 5.5 编写测试：JSON LIKE 搜索跨库一致性（SQLite vs PG）
- [x] 5.6 编写测试：单 DB 模式向后兼容（不设 `DATABASE_LOCAL_URL` 时行为一致）
- [x] 5.7 端到端测试：双 DB 模式下完整 Agent 运行（从 run 启动到 message 完整落盘）
- [x] 5.8 性能基准测试：对比单 DB（同步写 PG）vs 双 DB（直写 SQLite）的 per-token 延迟
- [x] 5.9 并行子任务写锁竞争基准测试：Orchestrator DAG 派发 N 个并行子任务，验证 SQLite 写锁不超时
- [x] 5.10 运行 `ruff check .` 和 `pytest` 确保所有测试通过

## 6. 文档同步

- [x] 6.1 更新 `specs/08-db-schema.md`：新增双 DB 表分类、跨库 FK 移除、SQLite WAL 配置说明
- [x] 6.2 更新 `openspec/specs/persistence/spec.md`：sync delta（双 DB 架构需求、表路由、进程内缓存）
- [x] 6.3 更新 `CLAUDE.md` §3.1 五层分层说明（L1 Persistence 双引擎说明）和 §2 基础设施表（移除 Redis 行）
- [x] 6.4 更新 `backend/.env.example`：移除 Redis 相关配置，新增 `DATABASE_LOCAL_URL`
- [x] 6.5 更新 `docs/dual-database-design.md` 状态从「设计提案」改为「已实施」
