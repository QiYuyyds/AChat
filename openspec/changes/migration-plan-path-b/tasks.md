## 1. Phase 0: PG 切换 + docker-compose

- [x] 1.1 创建 docker-compose.yml（PG + Milvus standalone + ES single-node + Neo4j community + etcd + minio）
- [x] 1.2 扩展 backend/app/config.py 配置字段（Milvus/ES/Neo4j/Kafka/Embedding/RAG/Memory 全部配置项）
- [x] 1.3 改造 backend/app/db/engine.py：删除 SQLite PRAGMA 钩子，切换 asyncpg 驱动，配置 PG 连接池参数
- [x] 1.4 改造 backend/app/db/models.py：JSON 列从 Text 升级为 JSONB 类型
- [x] 1.5 新增 6 张 ORM 模型到 models.py（LongTermMemory/UserPreference/RagChunk/ChatHistory/MemoryNode/MemoryEdge）
- [x] 1.6 更新 backend/requirements.txt 新增依赖（asyncpg/sqlalchemy[postgresql_asyncpg]/pymilvus/elasticsearch/neo4j/kafka-python）
- [x] 1.7 验证：docker-compose up -d 全部服务 healthy + FastAPI 启动连接 PG + 现有 API 正常运行（需要实际运行测试）

## 2. Phase 1: 记忆系统搬运

- [x] 2.1 创建 backend/app/memory/__init__.py 模块结构
- [x] 2.2 搬运 ShortTerm 到 backend/app/memory/short_term.py（纯内存 deque，零改动）
- [x] 2.3 搬运 consolidation 数据类到 backend/app/memory/consolidation.py（Item/RecallFilter/ConsolidationResult/ConsolidationConfig/_tokenize_zh，直接复用）
- [x] 2.4 搬运 LongTerm 到 backend/app/memory/long_term.py（搬运 + psycopg2→SQLAlchemy async session 改造）
- [x] 2.5 搬运 Preference 到 backend/app/memory/preference.py（搬运 + async 改造 + user_id 固定 default_user）
- [x] 2.6 搬运 GraphMemory 到 backend/app/memory/graph_memory.py（搬运 + sync neo4j→AsyncDriver + threading→asyncio.create_task）
- [x] 2.7 创建 backend/app/memory/memory_service.py MemoryService 装配入口（initialize + on_message_end）
- [x] 2.8 编写 ShortTerm 单元测试（add/get/clear/sliding window）
- [x] 2.9 编写 LongTerm 单元测试（add/recall/consolidate，mock embedding）
- [x] 2.10 编写 Preference 单元测试（extract_and_save 规则匹配）
- [x] 2.11 编写 GraphMemory 降级测试（Neo4j 不可用时 no-op）

## 3. Phase 2: RAG 系统搬运

- [x] 3.1 创建 backend/app/rag/__init__.py 模块结构
- [x] 3.2 搬运 RecursiveSplitter 到 backend/app/rag/splitter.py（纯算法，零改动）
- [x] 3.3 搬运 LLMRewriter 到 backend/app/rag/rewriter.py（纯逻辑，零改动）
- [x] 3.4 搬运 LLMReranker 到 backend/app/rag/reranker.py（纯逻辑，零改动）
- [x] 3.5 创建 backend/app/infra/__init__.py 模块结构
- [x] 3.6 搬运 HybridStore 到 backend/app/infra/hybrid.py（深度适配：PG→async session + threading→asyncio.gather + RRF 算法不变）
- [x] 3.7 创建 backend/app/rag/rag_engine.py RAG Engine 入口（切分→入库→检索→合成）
- [x] 3.8 创建 backend/app/services/rag_service.py RAGService 装配入口（ingest + search）
- [x] 3.9 在 backend/app/tools/registry.py 注册 rag_search/rag_ingest/memory_recall 三个新工具
- [x] 3.10 编写 RecursiveSplitter 单元测试（中英文混合切分 + Markdown 保护）
- [x] 3.11 编写 LLMRewriter/LLMReranker 单元测试（改写/精排 + 失败降级）
- [x] 3.12 编写 HybridStore 集成测试 + 降级测试（逐路关闭验证 RRF 权重归一）

## 4. Phase 3: Prompt 装配器

- [x] 4.1 创建 backend/app/services/prompt_assembler.py（Slot/Schema/Source/Assembler 全部内联）
- [x] 4.2 实现 6 种 SlotKind 定义 + SlotFilter/Slot/ContextItem/FilledSlot 数据类
- [x] 4.3 实现 4 种 Schema（CHAT/TOOL/REACT/RAG）+ slot_priority + DEFAULT_GLOBAL_TOKEN_BUDGET
- [x] 4.4 实现 6 种 ContextSource（ProfileSource/PlannerSource/TaskMemSource/ToolStateSource/ConstraintsSource/RecallSource）对接 AChat 数据源
- [x] 4.5 实现 ContextAssembler.assemble()（asyncio.gather 并发填充 + 异常隔离 + token 预算裁剪）
- [x] 4.6 实现 RuntimeContext.render_system_prompt() + render_history()（OpenAI chat 格式输出）
- [x] 4.7 改造 backend/app/services/conversation_context.py：build_history_for() 委托 PromptAssembler
- [x] 4.8 编写 4 种 Schema 装配测试 + 各 Source 独立测试 + token 预算裁剪测试

## 5. Phase 4: AgentRunner 集成 + Infrastructure 工厂

- [x] 5.1 创建 backend/app/infra/factory.py Infrastructure 工厂（build_infrastructure + status_summary + 配置驱动装配）
- [x] 5.2 创建 backend/app/infra/status.py InfrastructureStatus 状态聚合
- [x] 5.3 改造 backend/app/services/agent_runner.py：build_adapter_input() 对接 PromptAssembler
- [x] 5.4 改造 backend/app/services/agent_runner.py：finalize() 新增 _post_run_memory_hook（asyncio.create_task 后台记忆写入）
- [x] 5.5 改造 backend/app/main.py：启动时初始化 Infrastructure + MemoryService + RAGService + PromptAssembler
- [x] 5.6 实现启动状态仪表盘日志输出
- [x] 5.7 端到端验证：对话→记忆写入→跨会话召回→偏好提取→consolidate 触发

## 6. Phase 5: 测试 + 打磨

- [x] 6.1 降级测试：逐服务 kill（Milvus/ES/Neo4j/Kafka）验证对应能力降级 + 系统继续运行
- [x] 6.2 性能基准测试：PG JSONB 查询 / Milvus ANN / ES BM25 / Neo4j 遍历 / RRF 融合 / PromptAssembler 并发填充
- [x] 6.3 编写 scripts/migrate_sqlite_to_pg.py 数据迁移脚本（可选）
- [x] 6.4 更新 .env.example 新增所有配置项示例
- [x] 6.5 全量回归测试：确保现有 API + 前端对话流程不受影响
