# AChat 架构与目录说明

> 本文档描述项目的整体架构、目录结构与数据流，反映后端迁移到 Python (FastAPI) 并集成 RAG / 记忆 / 知识图谱 / Document 知识库体系后的最新状态。
>
> 协作规则见 [CLAUDE.md](./CLAUDE.md)，代码地图见 [OVERVIEW.md](./OVERVIEW.md)，详细契约见 [specs/](./specs/)。

---

## 1. 项目定位

**AChat** 是一个 local-first 的多 Agent 协作平台。一句话：

> 把多 Agent 协作做成 IM 群聊体验。Agent 是「联系人」，对话是「工作空间」，Orchestrator 是「群里的项目经理」。

**核心能力**：

- IM 范式会话管理（单聊 / 群聊 / @提及 / 搜索 / 置顶 / 归档 / 书签）
- 统一适配器层接入 Claude / Custom(OpenAI 兼容) / Mock Agent
- Orchestrator 自动拆任务、DAG 并行调度、聚合结果
- 产物（代码 / 网页 / 文档 / PPT / 图片）内联预览与二次编辑
- 每会话独立 workspace 沙箱（sandbox / local 双模式）
- **RAG 混合检索**（Milvus 向量 + Elasticsearch 全文 + Neo4j 知识图谱，RRF 融合）
- **分层记忆系统**（短期 / 长期 / 偏好 / 图谱记忆 + 自动固化与衰减）
- **Document + Version 知识库**（全局文档版本化、解析入库、按需召回）
- 桌面打包（Electron）+ 移动伴随端（Capacitor）

**运行形态**：前后端分离本地运行。前端 Next.js dev server（:3000），后端 FastAPI（:8000）；基础设施服务（PostgreSQL / Milvus / ES / Neo4j）通过 Docker Compose 启动，可全部容器化也可仅远端部署基础设施。

---

## 2. 技术栈

### 前端

| 层 | 选型 |
|---|---|
| 框架 | Next.js 16 App Router + React 19（锁定 `16.2.6`） |
| 语言 | TypeScript strict 模式 |
| 样式 | Tailwind CSS v4 + shadcn/ui |
| 状态 | Zustand + Immer middleware |
| 实时 | SSE（一条全局连接） |
| 包管理 | pnpm（workspace） |

### 后端

| 层 | 选型 |
|---|---|
| 框架 | FastAPI（Python 3.11+） |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| 验证 | Pydantic v2 + pydantic-settings |
| 数据库 | **PostgreSQL 16**（asyncpg 驱动） |
| AI SDK | `anthropic` · `openai`（Python SDK） |
| 包管理 | pip + venv（`pyproject.toml`） |

### 基础设施（Docker Compose）

| 服务 | 镜像 | 用途 |
|---|---|---|
| PostgreSQL | `postgres:16-alpine` | 关系型主库（17 张表） |
| Milvus | `milvusdb/milvus:v2.4.17` | 向量检索（RAG 语义 + LTM recall） |
| Elasticsearch | `elasticsearch:8.14.0` | 全文检索（RAG BM25） |
| Neo4j | `neo4j:5-community` | 知识图谱（KGStore + GraphMemory） |
| Kafka | 可选 | 事件总线增强（默认 in-process） |

> **降级策略**：每个基础设施服务独立 try/except，单个失败不影响其他。Milvus 挂 → 退化为 TF cosine；ES 挂 → 无全文检索；Neo4j 挂 → GraphMemory no-op；Kafka 不配 → 用 in-process EventBus。启动时打印状态面板。

---

## 3. 五层架构

```
┌──────────────────────────────────────────────────────────────────┐
│ L5  UI 组件 (React / shadcn)                  src/components/       │  ← 前端
│ L4  State + Transport (Zustand + SSE)         src/stores/ src/lib/  │  ← 前端
├──────────────────────────────────────────────────────────────────┤
│                    HTTP (REST + SSE)  ↕  跨进程边界                  │
├──────────────────────────────────────────────────────────────────┤
│ L3  Application Services                      backend/app/services/ │  ← Python
│     AgentRunner · Orchestrator · ConversationService ·             │
│     EventBus · ToolExecutor · RAGService · DocumentService ·       │
│     PromptAssembler · ...                                          │
│ L2  Agent Platform Adapters                   backend/app/adapters/ │  ← Python
│     Claude · Custom(OpenAI 兼容) · Mock                            │
│ L1  Persistence                               backend/app/db/       │  ← Python
│     SQLAlchemy + PostgreSQL + workspace 文件系统                    │
├──────────────────────────────────────────────────────────────────┤
│  Infrastructure Layer (可选, 独立降级)          backend/app/infra/   │
│  Milvus(向量) · Elasticsearch(全文) · Neo4j(图谱) · Kafka(事件)     │
│  └─ RAG 混合检索 (backend/app/rag/)  HybridStore + RRF              │
│  └─ 记忆系统 (backend/app/memory/)  STM/LTM/Preference/Graph        │
│  └─ 知识图谱 (backend/app/graph/)   KGStore + Extractor             │
└──────────────────────────────────────────────────────────────────┘
```

**铁律**：

- UI **永远不**直接调 LLM SDK，必须经过 L3
- Adapter **永远不**写 DB，它只负责事件流翻译
- 工具执行（ToolExecutor）属 L3，不是 Adapter 的事
- Orchestrator 是特殊 Agent，走同一个 AgentRunner，只是多了 `plan_tasks` / `report_task_result` 工具与不同 system prompt

---

## 4. 顶层目录地图

```
bitdance-agenthub-main/
├── backend/              ★ Python 后端 (L1-L3 + 适配器 + RAG + 记忆 + 图谱) —— 全部业务逻辑
├── src/                  前端 (L4-L5) + 共享类型
│   ├── app/              Next.js 页面 (layout / page)
│   ├── components/       63 个 React 组件
│   ├── lib/              api.ts (REST 客户端) · config.ts (API base) · 工具
│   ├── stores/           Zustand store (app-store / search-store)
│   ├── shared/           ★ 共享类型 (StreamEvent / MessagePart ...) 前后端契约源
│   └── db/schema.ts      仅保留前端 import 行类型 (DB 实体由后端 SQLAlchemy 拥有)
├── electron/             桌面版外壳 (main.ts / paths.ts / server-bootstrap.ts)
├── apps/mobile/          移动伴随 App (Capacitor)
├── packages/shared/      共享包 (workspace)
├── specs/                ★ 18 份编号详细规格 (语言无关契约)
├── openspec/             OpenSpec 能力契约 + 变更提案
├── skills/               可复用开发任务模板
├── scripts/              构建 / Electron / SQLite 辅助脚本 (.mjs)
├── docs/                 文档 + 图片
├── .agenthub-data/       运行时数据 (workspaces + deployments + skills)
├── docker-compose.yml            全栈容器化 (前后端 + 基础设施)
├── docker-compose.infra.yml      仅基础设施 (本机跑前后端, 远端跑 PG/Milvus/ES/Neo4j)
├── CLAUDE.md             ★ AI 协作规则 (怎么做 / 不做什么)
├── OVERVIEW.md           代码地图 (做了什么 / 在哪)
└── ARCHITECTURE.md       本文档
```

`★` = 理解项目最关键的入口。

---

## 5. 后端深度剖析 (`backend/`)

```
backend/
├── app/
│   ├── main.py              FastAPI 入口: 路由接线 + CORS + lifespan 启动全链路
│   │                        (init_db → build_infrastructure → MemoryService → RAGService
│   │                         → PromptAssembler → DocumentService → 状态面板)
│   ├── config.py           配置 (pydantic-settings) + .env key 桥接到 os.environ
│   │
│   ├── db/ (3)             【L1 持久化】
│   │   ├── models.py        17 张表 SQLAlchemy 模型 (9 核心 + 6 AGI-memory + 2 Document)
│   │   └── engine.py        异步引擎 + PostgreSQL (外键 ON / 连接池)
│   │
│   ├── schemas/ (7)        【类型契约 Pydantic】
│   │   ├── events.py        30+ StreamEvent (SSE 协议, snake_case + camelCase 别名)
│   │   ├── messages.py      MessagePart (parts 数组)
│   │   ├── artifacts.py     Artifact 内容类型
│   │   ├── dispatch.py      调度计划 / 任务
│   │   ├── document.py      Document / DocumentVersion
│   │   └── requests.py      API 请求 / 响应模型
│   │
│   ├── services/ (30)      【L3 业务逻辑 —— 核心大头】
│   │   ├── agent_runner.py        ★ 执行器 (simple 路径 + 共享机制)
│   │   ├── orchestrator.py        ★ 编排器三阶段调度 (PLAN / EXECUTE / AGGREGATE)
│   │   ├── orchestrator_prompts.py编排 prompt / XML 构建
│   │   ├── conversation_service.py会话 / 消息全生命周期
│   │   ├── event_bus.py           SSE 事件总线 (asyncio.Queue 扇出)
│   │   ├── dispatch_plan.py       计划校验 / DAG 拓扑
│   │   ├── conversation_context.py跨 run 历史注入
│   │   ├── artifact_service.py    产物 CRUD / 版本链
│   │   ├── deployment_service.py  产物部署 + 资源 / zip
│   │   ├── settings_service.py    全局设置 / API key 解析
│   │   ├── fs_service.py          workspace 文件读写 + 沙箱配额
│   │   ├── search_service.py      消息全文搜索
│   │   ├── task_result_report.py  子任务上报 + 完成度门禁
│   │   ├── rag_service.py         ★ RAG 混合检索 (Milvus + ES + KG + RRF)
│   │   ├── document_service.py    ★ Document + Version 知识库 CRUD
│   │   ├── prompt_assembler.py    ★ 上下文组装 (Profile + Recall + Constraints)
│   │   ├── skill_service.py       Agent Skills 加载 / 写入
│   │   ├── runner_registry.py     per-conversation runner 生命周期
│   │   ├── deploy_command_service.py 部署斜杠命令
│   │   ├── context_compaction_service.py 上下文压缩
│   │   ├── usage_summary_service.py Token 分析聚合
│   │   ├── network_hints.py       移动端网络发现
│   │   └── pending_*.py           审批 / 提问 / 命令 / 计划 内存 store
│   │
│   ├── adapters/ (8)       【L2 适配器】stream(input, cancel_event) -> AsyncIterator[StreamEvent]
│   │   ├── base.py          AdapterInput + ABC
│   │   ├── mock_adapter.py  Mock (脚本流, 不烧 token)
│   │   ├── custom_adapter.py OpenAI 兼容 (DeepSeek / 火山方舟等, 工具循环 MAX_TURNS=8)
│   │   ├── claude_adapter.py Anthropic Messages API
│   │   └── custom_provider_client.py / registry.py / session_store.py
│   │
│   ├── tools/ (18)         【工具系统】20 个内置工具
│   │   ├── base.py / registry.py  ToolContext (asyncio.Event 取消) + 注册表
│   │   ├── write_artifact / read_artifact / deploy_artifact / deploy_workspace
│   │   ├── fs_read / fs_write / fs_list / bash (黑名单 + 审批)
│   │   ├── ask_user / plan_tasks / report_task_result
│   │   ├── read_attachment (PDF: pypdf)
│   │   ├── web_search (Tavily API)
│   │   ├── memory_rag (memory_recall + rag_search/ingest/list/delete)
│   │   └── skills (load_skill / write_skill)
│   │
│   ├── rag/ (6)            【RAG 引擎】
│   │   ├── rag_engine.py    HybridStore: 向量(Milvus) + 全文(ES) + 图谱(KG) + RRF 融合
│   │   ├── parser.py        文档解析 (pdfplumber → PyPDF2 → pdftotext 三级降级)
│   │   ├── splitter.py      文档分块 (chunk_size / overlap)
│   │   ├── rewriter.py      Query Rewriting (LLM 生成扩展查询)
│   │   └── reranker.py      Reranking (LLM 打分重排)
│   │
│   ├── memory/ (7)         【分层记忆系统】
│   │   ├── memory_service.py  ★ 门面: STM + LTM + Preference + GraphMemory
│   │   ├── short_term.py      短期记忆 (chat_history 表, 滑动窗口)
│   │   ├── long_term.py       长期记忆 (long_term_memory 表, embedding 语义召回)
│   │   ├── preference.py      用户偏好 (user_preferences 表, KV)
│   │   ├── graph_memory.py    图谱记忆 (Neo4j + memory_nodes/edges 镜像表)
│   │   └── consolidation.py   记忆固化 / 去重 / 衰减 / TTL
│   │
│   ├── graph/ (4)          【知识图谱】
│   │   ├── kgstore.py       KGStore: 文档 → 实体/关系抽取 → Neo4j 入图 → 子图检索
│   │   ├── extractor.py     LLM 驱动的实体 / 关系抽取
│   │   └── types.py         图谱类型定义
│   │
│   ├── infra/ (4)          【基础设施工厂】
│   │   ├── factory.py       build_infrastructure(): 配置驱动, 独立降级
│   │   ├── hybrid.py        HybridStore 抽象 (向量 + 全文 + 图谱统一接口)
│   │   └── status.py        基础设施连接状态面板
│   │
│   ├── api/ (14)           【API 路由】
│   │   ├── conversations / messages / agents / artifacts / attachments
│   │   ├── fs / pending / settings / runs_misc / stream (SSE)
│   │   ├── documents / skills / deployments
│   │   └── mobile/routes
│   │
│   └── utils/ (13)         跨平台 · 安全黑名单 · ID · token 估算 · 审批 helper · mermaid 规范化 ...
│
└── tests/ (31+)           pytest 测试; ruff 全绿
```

### 关键技术映射（TS → Python）

| TypeScript (旧) | Python (现) |
|---|---|
| Drizzle ORM | SQLAlchemy 2.0 |
| Zod | Pydantic v2 |
| AsyncIterable | async generators |
| AbortSignal | asyncio.Event |
| EventEmitter | asyncio.Queue + 订阅者 |
| Promise / Future | asyncio.Task / Future |
| `Date.now()` | `now_ms()` |
| better-sqlite3 | asyncpg (PostgreSQL) |

> **数据契约**：DB 内 JSON（parts / agent_ids / usage）与 SSE 事件**全程 camelCase**；Pydantic 用 snake_case 字段 + camelCase 别名（`populate_by_name=True`），与前端字节兼容。

---

## 6. 数据库：17 张表

### 核心域（9 张）

| 表 | 说明 |
|---|---|
| `agents` | AI 代理（name / adapter_name / system_prompt / tool_names / skill_names / api_key） |
| `conversations` | 会话（mode single/group / agent_ids / pinned / bookmarked / archived / rag_enabled） |
| `messages` | 消息（role / parts JSON / status / run_id / usage） |
| `artifacts` | 产物（type / content JSON / version / parent_artifact_id） |
| `workspaces` | 工作区（mode sandbox/local / root_path / bound_path） |
| `attachments` | 附件（kind image/file / file_path / mime_type） |
| `agent_runs` | 运行记录（status / usage / dispatch_plan / dispatch_results / parent_run_id） |
| `conversation_context_summaries` | 上下文压缩摘要 |
| `app_settings` | 全局设置单行表（各 provider API key + 部署配置 + companion） |

### AGI-memory 新增（6 张）

| 表 | 说明 |
|---|---|
| `long_term_memory` | 长期记忆（content / importance / embedding / category / tags / score） |
| `user_preferences` | 用户偏好 KV（user_id / key / value） |
| `rag_chunks` | RAG 文档分块（doc_hash / chunk_idx / content / embedding / document_id / version_id / content_hash） |
| `chat_history` | 短期记忆持久化（role / content） |
| `memory_nodes` | 记忆图谱节点（Neo4j 镜像表） |
| `memory_edges` | 记忆图谱边（from_id / to_id / rel_type / weight） |

### Document + Version 知识库（2 张）

| 表 | 说明 |
|---|---|
| `documents` | 全局知识库文档（title / doc_type / source / status / latest_version_id） |
| `document_versions` | 文档版本（document_id / version / content_md / summary / metadata） |

---

## 7. 一条消息的生命周期（数据流）

```
用户在 UI 输入并发送
  └─ src/lib/api.ts  POST /api/conversations/{id}/messages
       └─ L3 conversation_service.send_message()
            ├─ 持久化用户 message
            ├─ 决策响应者 (单聊 / 群聊)
            └─ runner_registry → AgentRunner.run()  (起 asyncio.Task, 立即返回)
                 └─ agent_runner.execute_run()
                      ├─ build_adapter_input()  历史注入 + token 预算 + key 选择
                      │   └─ (可选) PromptAssembler 注入 Profile + Recall + Constraints
                      ├─ adapter.stream()  ← L2 (Claude / Custom / Mock)
                      │     产出 StreamEvent: part.delta / tool.call / artifact.create ...
                      │     工具调用 → tool_registry.execute() (沙箱内)
                      └─ consume_stream()
                           ├─ persist_event()  事件落 DB
                           └─ event_bus.publish()  → SSE
                                └─ GET /api/stream (一条全局连接)
                                     └─ 前端 stream-provider.tsx onmessage
                                          └─ Zustand store.applyEvent()  → UI 实时更新
```

**编排场景**：若目标 agent 是 Orchestrator，`execute_run` 转 `orchestrator.execute_orchestrator_run()` → **PLAN**（plan_tasks）→ 人工审批 → **EXECUTE**（DAG 调度 + 并发 + 子任务重试 + 冲突检测）→ **AGGREGATE**。

---

## 8. RAG 混合检索数据流

```
文档入库:
  Document (PG) → DocumentVersion (PG)
    └─ parser.py 解析 (pdfplumber → PyPDF2 → pdftotext)
       └─ splitter.py 分块
          └─ embedding API → 向量
             ├─ rag_chunks (PG, content_hash 缓存)
             ├─ Milvus insert (向量索引, COSINE)
             ├─ Elasticsearch index (全文, BM25)
             └─ KGStore.index_document (Neo4j, LLM 抽取实体/关系入图)

查询召回:
  user query
    └─ (可选) rewriter.py LLM 扩展查询
       └─ 并行检索:
          ├─ Milvus search (语义相似度)
          ├─ Elasticsearch search (全文匹配)
          └─ KGStore.search (图谱子图遍历, max_hops)
       └─ RRF 融合 (semantic_weight 加权)
          └─ (可选) reranker.py LLM 重排
             └─ 返回 top_k chunks → 注入 Agent 上下文
```

---

## 9. 记忆系统数据流

```
对话产生消息
  └─ ShortTermMemory 记录 (chat_history 表, 滑动窗口 max_turns)
     └─ 触发固化 (trigger 阈值)
        └─ ConsolidationService:
           ├─ 去重 (cosine 相似度 > dedup 阈值 → 合并)
           ├─ 衰减 (importance *= decay_rate, 低于 min → 清理)
           └─ 写入 LongTermMemory (long_term_memory 表, embedding 向量)
              └─ GraphMemory 抽取实体/关系 → Neo4j + memory_nodes/edges 镜像表

Agent 运行时注入 (PromptAssembler):
  ProfileSource (UserPreference)  → 用户偏好
  RecallSource (LTM + GraphMemory) → 语义召回相关记忆
  ConstraintsSource               → 约束规则
  → 组装为 system prompt 补充段
```

---

## 10. 前端结构 (`src/`)

| 目录 | 内容 |
|---|---|
| `app/` | `layout.tsx` / `page.tsx`（挂载 StreamProvider + 主界面） |
| `components/` (63) | ChatPanel / MessageList / MessageParts / ArtifactPreviewPanel / AgentLibrary / CreateAgentDialog / DispatchPlanCard / KnowledgeLibrary / DocumentDetail / UploadDocumentDialog / SkillLibrary / GlobalSearch / SettingsDialog ... |
| `lib/` | `api.ts`（REST 客户端，统一 `API_BASE_URL` 前缀）· `config.ts`（读 `NEXT_PUBLIC_API_BASE_URL`）· 工具 |
| `stores/` | `app-store.ts`（会话 / 消息 / 事件 reducer）· `search-store.ts` |
| `shared/` (14) | StreamEvent / MessagePart / Artifact 等**前后端共享类型**（纯类型，无逻辑） |
| `db/schema.ts` | 仅保留前端 import 行类型（AgentRow 等） |

**前后端边界**：前端只通过 `lib/api.ts`（REST）和 `stream-provider.tsx`（SSE EventSource）与 Python 后端通信，两者都加 `API_BASE_URL` 前缀；默认空串 = 同源，设环境变量即指向独立 Python 后端。

---

## 11. 其它目录

| 目录 | 说明 | 当前状态 |
|---|---|---|
| `specs/` | 18 份编号详细规格（实体 / 事件 / 适配器 / 工具 / 编排 / DB ...），**语言无关契约** | 有效 |
| `openspec/` | OpenSpec 能力契约 + 变更提案（`changes/`） | 有效 |
| `electron/` | 桌面版（`main.ts` 启动内嵌 Next server） | ⚠️ 待改造：内嵌 Next 已无后端，需改启 Python |
| `apps/mobile/` | 移动伴随 App（Capacitor / 远程审批，spec 14） | 独立模块 |
| `scripts/` | 构建 / Electron / SQLite ABI 辅助（`.mjs`） | 前端用 |
| `skills/` | 可复用开发任务模板（add-adapter / add-tool ...） | 参考 |
| `.agenthub-data/` | 运行时：`workspaces/` + `deployments/` + `skills/` | 前后端共用 |
| `待融合项目/AGI-memory/` | AGI-memory 源项目（记忆 / RAG / 图谱能力的来源） | 参考 |

---

## 12. 如何运行

### 最小启动（仅前后端，无 RAG / 记忆 / 图谱）

**后端（终端 A）**
```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

**前端（终端 B）**
```powershell
$env:NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"; pnpm dev
```

浏览器打开 `http://localhost:3000`。

### 完整启动（含基础设施）

**基础设施（终端 A）**
```powershell
docker compose -f docker-compose.infra.yml up -d
```

**后端（终端 B）**——配置 `backend/.env` 指向本地基础设施
```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

**前端（终端 C）**
```powershell
$env:NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"; pnpm dev
```

### API Key 优先级

1. **`agents.api_key`** — per-agent override（最高优先级）
2. **`app_settings.<provider>_api_key`** — 设置面板全局自填
3. **`backend/.env`** — 环境变量兜底

```env
# backend/.env
DATABASE_URL=postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
ARK_API_KEY=...
TAVILY_API_KEY=...           # web_search 工具
EMBEDDING_API_KEY=...        # RAG / LTM 语义检索
EMBEDDING_API_URL=...
EMBEDDING_MODEL=...
MILVUS_HOST=localhost        # 留空 = 禁用 Milvus
ES_ADDRESSES=http://localhost:9200  # 留空 = 禁用 ES
NEO4J_URI=bolt://localhost:7687     # 留空 = 禁用 Neo4j
ENABLE_GRAPH=false           # true 才启用知识图谱
```

---

## 13. 基础设施降级矩阵

| 服务 | 配置为空时 | 影响 |
|---|---|---|
| PostgreSQL | — (必需) | 后端无法启动 |
| Milvus | `MILVUS_HOST` 空 | RAG 向量检索退化；LTM 退化为 TF cosine |
| Elasticsearch | `ES_ADDRESSES` 空 | RAG 无全文检索 |
| Neo4j | `NEO4J_URI` 空 或 `ENABLE_GRAPH=false` | GraphMemory no-op；RAG 无图谱检索 |
| Kafka | `KAFKA_BROKERS` 空 | 用 in-process EventBus（默认） |
| Embedding API | `EMBEDDING_API_KEY` 空 | RAG / LTM 无语义检索能力 |
| LLM API (RAG 用) | 无任何 LLM key | RAG 无 rewrite / rerank；KG 无实体抽取 |

> 启动时后端打印状态面板，一目了然哪些服务已连接、哪些降级。

---

*本文档由整体目录与代码分析生成。深入某子系统请读 `specs/` 对应编号；协作规则见 [CLAUDE.md](./CLAUDE.md)；代码地图见 [OVERVIEW.md](./OVERVIEW.md)。*
