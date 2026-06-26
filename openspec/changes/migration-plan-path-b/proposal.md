## Why

AgentHub 当前使用 SQLite 作为唯一持久化层，缺乏向量检索、全文检索、知识图谱和结构化记忆能力。AGI-memory 项目已实现成熟的三层记忆系统（STM/LTM/Preference + Graph 增强）、三路 RRF 融合 RAG 检索（Milvus + ES + Neo4j）以及 Schema 驱动的 Prompt 装配器。将 AGI-memory 的核心能力融合进 AgentHub，可大幅提升 Agent 的上下文理解、知识检索和对话连贯性，同时保持单用户 local-first 架构的简洁性。

## What Changes

- **BREAKING**: SQLite 全量替换为 PostgreSQL（asyncpg 驱动），现有 9 张业务表迁移至 PG，JSON 列升级为 JSONB
- 新增 6 张 ORM 模型（LongTermMemory、UserPreference、RagChunk、ChatHistory、MemoryNode、MemoryEdge）
- 新增三层记忆系统：ShortTerm（deque 滑动窗口）+ LongTerm（embedding cosine + consolidate 三阶段）+ Preference（规则提取）+ GraphMemory（Neo4j 图增强）
- 新增 RAG 系统：RecursiveSplitter 文档切分 + HybridStore 三路 RRF 融合检索（Milvus ANN + ES BM25/IK + Neo4j 图路）+ LLMRewriter 查询改写 + LLMReranker 精排
- 新增 Prompt 装配器：6 种 SlotKind + 4 种 Schema + 6 种 Source，Schema 驱动上下文装配替代硬编码拼装
- 新增 docker-compose.yml 全套服务编排（PG + Milvus + ES + Neo4j + 可选 Kafka）
- 新增 Infrastructure 工厂：配置驱动装配 + 独立降级链路
- 新增 3 个 Agent 工具：rag_search、rag_ingest、memory_recall
- AgentRunner 集成记忆写入 hook + PromptAssembler 对接
- 前端零改动，API 契约不变

## Capabilities

### New Capabilities

- `memory-system`: 三层记忆系统（STM/LTM/Preference）+ GraphMemory 图增强 + consolidate 三阶段算法
- `rag-system`: RAG 检索全栈（文档切分 + 多路入库 + RRF 融合检索 + LLM 改写/精排）
- `prompt-assembler`: Schema 驱动的 Prompt 上下文装配器（Slot/Schema/Source/Assembler）
- `infrastructure-factory`: 基础设施连接管理工厂（Milvus/ES/Neo4j/Kafka 配置驱动装配 + 降级链路）

### Modified Capabilities

- `persistence`: 存储层从 SQLite 切换至 PostgreSQL（asyncpg），JSON 列升级为 JSONB，新增 6 张表
- `conversation-context`: 对接 PromptAssembler 替代硬编码 context 拼装
- `tools`: 注册 rag_search / rag_ingest / memory_recall 三个新工具
- `core-domain`: 新增 LongTermMemory、UserPreference、RagChunk、ChatHistory、MemoryNode、MemoryEdge ORM 模型

## Impact

- **后端核心文件改动**: config.py（扩展配置）、engine.py（asyncpg 切换）、models.py（JSONB + 新模型）、conversation_context.py（PromptAssembler 对接）、agent_runner.py（记忆 hook + PromptAssembler）、main.py（启动初始化）、tools/registry.py（新工具注册）
- **新增模块**: backend/app/infra/、backend/app/memory/、backend/app/rag/、backend/app/services/prompt_assembler.py
- **新增依赖**: asyncpg、pymilvus、elasticsearch[async]、neo4j、kafka-python（可选）
- **基础设施**: 新增 docker-compose.yml（PG + Milvus + ES + Neo4j），最低 6-8GB 内存
- **前端**: 零改动，API 契约不变
- **Electron**: 可保留，作为本地服务集群壳连接 docker-compose
