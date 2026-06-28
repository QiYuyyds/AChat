## Context

AChat 是 local-first 单用户 AI Agent 协作平台，当前使用 SQLite 作为唯一持久化层，上下文拼装通过 `conversation_context.py` 硬编码完成，无向量检索、全文检索、知识图谱或结构化记忆能力。

AGI-memory 是独立实现的 Agent 记忆/RAG/Prompt 装配系统，包含：三层记忆（STM deque 滑动窗口、LTM embedding cosine + consolidate 三阶段、Preference 规则提取）+ GraphMemory（Neo4j 图增强）、三路 RRF 融合 RAG（Milvus ANN + ES BM25/IK + Neo4j 图路 + LLM 改写/精排）、Schema 驱动 Prompt 装配器（6 SlotKind + 4 Schema + 6 Source + token 预算裁剪）。

本项目（路径 B）将 AGI-memory 核心能力融合进 AChat，SQLite 全量替换为 PostgreSQL，保持单用户设计（`user_id="default_user"`），前端零改动。

## Goals / Non-Goals

**Goals:**

- SQLite 全量替换为 PostgreSQL（asyncpg），JSON 列升级 JSONB，现有 9 张表 + 新增 6 张表
- 从 AGI-memory 搬运三层记忆系统（STM/LTM/Preference/GraphMemory）并 async 改造
- 从 AGI-memory 搬运 RAG 系统（Splitter/HybridStore/Rewriter/Reranker）并 async 改造
- 从 AGI-memory 搬运 Prompt 装配器（Slot/Schema/Source/Assembler）并对接 AChat 数据源
- 构建 Infrastructure 工厂实现配置驱动装配 + 独立降级链路
- docker-compose 全套服务编排（PG + Milvus + ES + Neo4j + 可选 Kafka）
- AgentRunner 集成记忆写入 hook + PromptAssembler 对接
- 注册 rag_search / rag_ingest / memory_recall 三个新 Agent 工具

**Non-Goals:**

- 用户认证/授权/RBAC/多租户
- 前端改动（API 契约不变）
- 离线模式保留
- AGI-memory 的 agent/router、handler、sandbox、tools、platform 模块搬运
- Kafka 作为 InProcess EventBus 的替代（Kafka 仅为可选增强）
- 生产级高可用部署

## Decisions

### D1: SQLite → PostgreSQL 全量替换（非混合模式）

**选择**: 全量替换 SQLite 为 PostgreSQL（asyncpg 驱动）
**理由**: JSONB 二进制存储比 SQLite JSON 文本查询快 10-100x；asyncpg 连接池成熟；AGI-memory 已基于 psycopg2/PG 开发，减少适配成本
**替代方案**: 保留 SQLite + 外挂 PG → 增加双写复杂度和一致性风险，不值得

### D2: 搬运策略分三类（A 直接复用 / B sync→async / C 深度适配）

**选择**: 按模块纯度和架构差异分类搬运
- A 类（纯算法/内存）：ShortTerm、RecursiveSplitter、LLMRewriter、LLMReranker、数据类 → 零改动
- B 类（DB 读写）：LongTerm、Preference、GraphMemory → psycopg2/threading → SQLAlchemy async session
- C 类（架构差异大）：HybridStore、PromptAssembler Source、Infrastructure → 重写接口层
**理由**: 最小化改动风险，纯算法代码不引入 bug

### D3: Infrastructure 工厂采用配置驱动 + 独立降级

**选择**: 每个外部服务（Milvus/ES/Neo4j/Kafka）独立 try/except 连接，失败不阻塞启动
**理由**: local-first 产品必须容忍部分基础设施不可用；降级链路已在 AGI-memory 中验证
**降级规则**: Milvus → TF cosine 内存暴力搜索；ES → 无全文检索；Neo4j → GraphMemory no-op；Kafka → 仅 InProcess

### D4: 并发模型从 threading 迁移到 asyncio

**选择**: HybridStore search_multi 中 threading.Thread → asyncio.gather；GraphMemory _go_safe → asyncio.create_task
**理由**: AChat 后端已全 async（FastAPI + async SQLAlchemy），引入 threading 会破坏一致性

### D5: Prompt 装配器 Source 适配 AChat 数据源

**选择**: 6 种 Source 分别对接 AChat 现有数据
- ProfileSource → agents.system_prompt + Preference
- PlannerSource → orchestrator dispatch_plan
- TaskMemSource → agent_runner run context
- ToolStateSource → agent_runs + tool_registry
- ConstraintsSource → workspace fs_write_approval_mode
- RecallSource → memory_service.ltm.recall_by_filter
**理由**: 保持 AGI-memory 装配器架构优势，同时复用 AChat 已有数据

### D6: docker-compose 作为基础设施编排

**选择**: 项目根目录 docker-compose.yml 包含 PG + Milvus（standalone）+ ES（single-node）+ Neo4j（community）+ 可选 Kafka
**理由**: 开发环境一键启动，Electron 可保留作为本地服务集群壳连接 docker-compose

## Risks / Trade-offs

**[R1] 内存/资源需求大幅增加** → 最低 6-8GB 内存（ES 1.5GB + Milvus 1GB + Neo4j 1GB + PG 500MB + App 500MB）。Mitigation: 配置驱动，不启用的服务不占资源；提供仅 PG 的最小部署模式。

**[R2] 搬运 async 改造可能引入并发 bug** → LongTerm consolidate 三阶段 + GraphMemory 图遍历改为 async 后可能遇到竞态。Mitigation: consolidate 加 asyncio.Lock；GraphMemory 降级为 no-op 时不抛异常；完善单元测试覆盖。

**[R3] AGI-memory 与 AChat 的 embedding 模型差异** → AGI-memory 默认 1024 维，AChat 需确认 Embedding API 可用性。Mitigation: 配置化 embedding_model/rag_milvus_dim；无 Embedding API 时降级为 TF-IDF 近似。

**[R4] PG 为 hard dependency 不可降级** → PG 挂了系统不可用。Mitigation: PG 稳定性远超 SQLite；可做主从复制；docker-compose healthcheck 保障启动。

**[R5] 数据迁移对现有用户的影响** → SQLite → PG 需要数据迁移。Mitigation: 提供 `scripts/migrate_sqlite_to_pg.py` 迁移脚本；标记为可选（新用户直接从 PG 开始）。
