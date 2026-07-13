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
- **用户认证与多用户隔离**（JWT + bcrypt · CSRF 防护 · 所有用户数据 `user_id` 隔离）
- **RAG 混合检索**（Milvus 向量 + Elasticsearch 全文 + Neo4j 知识图谱，RRF 融合）
- **分层记忆系统**（短期 / 长期 / 偏好 / 图谱记忆 + 自动固化与衰减）
- **Document + Version 知识库**（全局文档版本化、解析入库、按需召回）
- **Redis 元数据缓存 + 异步 DB 写入**（KV cache + Stream write-behind，可选降级）
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
| AI 适配器 | Claude Code / Codex 走 **CLI 子进程**（stream-json / JSON-RPC 2.0）；Custom 走 `openai` Python SDK |
| 包管理 | pip + venv（`pyproject.toml`） |

### 基础设施（Docker Compose）

| 服务 | 镜像 | 用途 |
|---|---|---|
| PostgreSQL | `postgres:16-alpine` | 关系型主库（22 张表） |
| Milvus | `milvusdb/milvus:v2.4.17` | 向量检索（RAG 语义 + LTM recall） |
| Elasticsearch | `elasticsearch:8.14.0` | 全文检索（RAG BM25） |
| Neo4j | `neo4j:5-community` | 知识图谱（KGStore + GraphMemory） |
| Kafka | 可选 | 事件总线增强（默认 in-process） |
| Redis | `redis:7-alpine` | 元数据缓存 + 异步 DB 写入（KV cache / Stream write-behind） |

> **降级策略**：每个基础设施服务独立 try/except，单个失败不影响其他。Milvus 挂 → 退化为 TF cosine；ES 挂 → 无全文检索；Neo4j 挂 → GraphMemory no-op；Kafka 不配 → 用 in-process EventBus；Redis 不配 → 退化为同步 DB 读写。启动时打印状态面板。

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
│     PromptAssembler · HookRegistry (生命周期 Hooks) · ...           │
│ L2  Agent Platform Adapters                   backend/app/adapters/ │  ← Python
│     ClaudeCLI · CodexCLI (CLI 子进程) · Custom (SDK) · Mock         │
│ L1  Persistence                               backend/app/db/       │  ← Python
│     SQLAlchemy + PostgreSQL + workspace 文件系统                    │
├──────────────────────────────────────────────────────────────────┤
│  Infrastructure Layer (可选, 独立降级)          backend/app/infra/   │
│  Milvus(向量) · Elasticsearch(全文) · Neo4j(图谱) · Kafka(事件)     │
│  Redis(元数据缓存 + 异步 DB 写入)                                    │
│  └─ RAG 混合检索 (backend/app/rag/)  HybridStore + RRF              │
│  └─ 记忆系统 (backend/app/memory/)  STM/LTM/Preference/Graph        │
│  └─ 知识图谱 (backend/app/graph/)   KGStore + Extractor             │
└──────────────────────────────────────────────────────────────────┘
```

**铁律**：

- UI **永远不**直接调 LLM SDK，必须经过 L3
- Adapter **永远不**写 DB，它只负责事件流翻译
- 工具执行（ToolExecutor）属 L3，不是 Adapter 的事
- 所有 Agent 走统一 Agent Loop（`run_agent_loop`）：solo / coordinated / subagent 三种模式共用一个 while-loop，任何 Agent 都能通过 `task_dispatch` 克隆自己处理子任务，Orchestrator（coordinated 模式）额外拥有 `dispatch_plan`（DAG 派发）。旧三阶段流程已删除（详见 `specs/19`）

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
│   │   ├── models.py        22 张表 SQLAlchemy 模型 (14 核心 + 6 AGI-memory + 2 Document)
│   │   └── engine.py        异步引擎 + PostgreSQL (外键 ON / 连接池)
│   │
│   ├── schemas/ (6)        【类型契约 Pydantic】
│   │   ├── events.py        30+ StreamEvent (SSE 协议, snake_case + camelCase 别名)
│   │   ├── messages.py      MessagePart (parts 数组)
│   │   ├── artifacts.py     Artifact 内容类型
│   │   ├── dispatch.py      调度计划 / 任务
│   │   ├── document.py      Document / DocumentVersion
│   │   └── requests.py      API 请求 / 响应模型
│   │
│   ├── services/ (34+)     【L3 业务逻辑 —— 核心大头】
│   │   ├── agent_runner.py        ★ 执行器 (execute_run 路由 + execute_simple_run ReAct loop)
│   │   ├── agent_loop.py          ★ 统一 Agent Loop (run_agent_loop: solo/coordinated/subagent)
│   │   │                          spawn_subagent_loop (递归子 Agent 派发) + prompt builders
│   │   ├── dag_executor.py        ★ DAG 验证 / 波调度 / 并行执行 (validate_dag / topological_waves / execute_dag)
│   │   ├── orchestrator.py        stub (旧三阶段已移除, 仅保留壳)
│   │   ├── orchestrator_prompts.py工具函数 (extract_text_from_parts 等)
│   │   ├── conversation_service.py会话 / 消息全生命周期
│   │   ├── event_bus.py           SSE 事件总线 (asyncio.Queue 扇出)
│   │   ├── conversation_context.py跨 run 历史注入 (hidden 消息过滤)
│   │   ├── artifact_service.py    产物 CRUD / 版本链
│   │   ├── deployment_service.py  产物部署 + 资源 / zip
│   │   ├── settings_service.py    全局设置 / API key 解析
│   │   ├── global_settings_service.py 全局设置缓存 (Redis 优先)
│   │   ├── async_db_writer.py     ★ Redis Stream 异步 DB 写入 (write-behind)
│   │   ├── recovery_scan.py       ★ 启动崩溃恢复 (streaming 消息扫描)
│   │   ├── fs_service.py          workspace 文件读写 + 沙箱配额
│   │   ├── search_service.py      消息全文搜索
│   │   ├── rag_service.py         ★ RAG 混合检索 (Milvus + ES + KG + RRF)
│   │   ├── document_service.py    ★ Document + Version 知识库 CRUD
│   │   ├── prompt_assembler.py    ★ 上下文组装 (Profile + Recall + Constraints)
│   │   ├── skill_service.py       Agent Skills 加载 / 写入
│   │   ├── runner_registry.py     per-conversation runner 生命周期
│   │   ├── deploy_command_service.py 部署斜杠命令
│   │   ├── context_compaction_service.py 上下文压缩
│   │   ├── usage_summary_service.py Token 分析聚合
│   │   ├── checkpoint_service.py  SDK Agent turn 级检查点保存/恢复
│   │   ├── hook_registry.py       ★ 生命周期 Hook 注册与分发
│   │   ├── project_artifact.py    项目产物管理
│   │   ├── agent_load_tracker.py  Agent 负载追踪
│   │   ├── network_hints.py       移动端网络发现
│   │   ├── hooks/                 ★ 内置 Hook 实现 (7 个)
│   │   │   ├── audit_log.py       审计日志
│   │   │   ├── auto_compact.py    自动上下文压缩
│   │   │   ├── checkpoint.py      检查点保存
│   │   │   ├── memory_persist.py  记忆持久化
│   │   │   ├── skill_auto_activator.py 技能自动激活
│   │   │   ├── summary_generate.py 摘要生成
│   │   │   └── tool_approval.py   工具审批拦截
│   │   └── pending_*.py           审批 / 提问 / 命令 / 计划 内存 store
│   │
│   ├── adapters/ (11)      【L2 适配器】stream(input, cancel_event) -> AsyncIterator[StreamEvent]
│   │   ├── base.py          AdapterInput + ABC + AdapterName (事件流契约)
│   │   ├── cli_base.py      ★ CLI 适配器公共基类 (子进程生命周期 / 管道 / 超时取消 / 参数过滤)
│   │   ├── conpty.py        Windows ConPTY 支持 (隐藏窗口 / 伪终端)
│   │   ├── claude_adapter.py ★ ClaudeCLIAdapter: spawn `claude` stream-json 协议
│   │   ├── codex_adapter.py  ★ CodexCLIAdapter: spawn `codex app-server` JSON-RPC 2.0
│   │   ├── mock_adapter.py  Mock (脚本流, 不烧 token)
│   │   ├── custom_adapter.py OpenAI 兼容 (DeepSeek / 火山方舟等, SDK 路线, 工具循环 MAX_TURNS=8)
│   │   └── custom_provider_client.py / registry.py / session_store.py
│   ├── mcp_bridge.py      ★ AChat MCP Bridge: stdio MCP Server, 把 write_artifact/ask_user/task_dispatch 等平台工具暴露给 CLI agent
│   │
│   ├── tools/ (22)         【工具系统】24 个内置工具
│   │   ├── base.py / registry.py  ToolContext (asyncio.Event 取消) + 注册表
│   │   ├── write_artifact / read_artifact / deploy_artifact / deploy_workspace
│   │   ├── fs_read / fs_write / fs_edit / fs_list / fs_glob / fs_grep / bash (黑名单 + 审批)
│   │   ├── task_dispatch (子 Agent 克隆派发) / dispatch_plan (DAG 派发)
│   │   ├── ask_user
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
│   ├── memory/ (8)         【分层记忆系统】
│   │   ├── memory_service.py  ★ 门面: STM + LTM + Preference + GraphMemory
│   │   ├── short_term.py      短期记忆 (chat_history 表, 滑动窗口)
│   │   ├── long_term.py       长期记忆 (long_term_memory 表, embedding 语义召回)
│   │   ├── preference.py      用户偏好 (user_preferences 表, KV)
│   │   ├── graph_memory.py    图谱记忆 (Neo4j + memory_nodes/edges 镜像表)
│   │   ├── memory_writer.py   记忆写入门面
│   │   └── consolidation.py   记忆固化 / 去重 / 衰减 / TTL
│   │
│   ├── graph/ (4)          【知识图谱】
│   │   ├── kgstore.py       KGStore: 文档 → 实体/关系抽取 → Neo4j 入图 → 子图检索
│   │   ├── extractor.py     LLM 驱动的实体 / 关系抽取
│   │   └── types.py         图谱类型定义
│   │
│   ├── infra/ (7)          【基础设施工厂】
│   │   ├── factory.py       build_infrastructure(): 配置驱动, 独立降级
│   │   │                   (Milvus/ES/Neo4j/Kafka/**Redis**)
│   │   ├── hybrid.py        HybridStore 抽象 (向量 + 全文 + 图谱统一接口)
│   │   ├── cache.py         ★ Redis KV 元数据缓存 (read-through + write-invalidation)
│   │   ├── cache_helpers.py ★ 缓存实体查找 (Agent/Settings/Workspace/GlobalSettings cached)
│   │   ├── cache_metrics.py 嵌入缓存命中率指标
│   │   └── status.py        基础设施连接状态面板
│   │
│   ├── api/ (15)           【API 路由】
│   │   ├── conversations / messages / agents / artifacts / attachments
│   │   ├── fs / pending / settings / runs_misc / stream (SSE)
│   │   ├── documents / skills / deployments / **auth**
│   │   └── mobile/routes
│   │
│   └── utils/ (13)         跨平台 · 安全黑名单 · ID · token 估算 · 审批 helper · mermaid 规范化 ...
│
└── tests/ (85+)           pytest 测试; ruff 全绿
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

## 6. 数据库：22 张表

### 用户域（1 张）

| 表 | 说明 |
|---|---|
| `users` | 用户（username / email / password_hash / token_version / display_name / avatar） |

### 核心域（10 张）

| 表 | 说明 |
|---|---|
| `agents` | AI 代理（name / adapter_name / system_prompt / tool_names / skill_names / hook_names / api_key / executable_path / protocol_family / custom_args / **user_id**） |
| `conversations` | 会话（mode single/group / agent_ids / pinned / bookmarked / archived / rag_enabled / summary / dispatch_mode / **user_id**） |
| `messages` | 消息（role / parts JSON / status / run_id / usage / hidden） |
| `artifacts` | 产物（type / content JSON / version / parent_artifact_id） |
| `workspaces` | 工作区（mode sandbox/local / root_path / bound_path） |
| `attachments` | 附件（kind image/file / file_path / mime_type） |
| `agent_runs` | 运行记录（status / usage / dispatch_plan / dispatch_results / parent_run_id） |
| `agent_run_checkpoints` | SDK Agent turn 级检查点（run_id / turn_number / messages_json） |
| `conversation_context_summaries` | 上下文压缩摘要 |
| `app_settings` | 全局设置单行表（各 provider API key + 部署配置 + companion） |

### 设置域（3 张）

| 表 | 说明 |
|---|---|
| `global_settings` | 全局部署配置（deployment_publish_enabled / deployment_publish_dir / deployment_public_base_url） |
| `user_settings` | 用户级设置（user_id / 各 provider API key / companion_mode / mobile_device_token） |
| `mcp_servers` | MCP Server 配置（user_id / name / command / args / env / transport_type） |

### AGI-memory 新增（6 张）

| 表 | 说明 |
|---|---|
| `long_term_memory` | 长期记忆（content / importance / embedding / category / tags / score / **user_id**） |
| `user_preferences` | 用户偏好 KV（**user_id** / key / value） |
| `rag_chunks` | RAG 文档分块（doc_hash / chunk_idx / content / embedding / document_id / version_id / content_hash / **user_id**） |
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
  └─ src/lib/api.ts  POST /api/conversations/{id}/messages (JWT cookie 认证 + Origin CSRF 检查)
       └─ L3 conversation_service.send_message()
            ├─ 持久化用户 message
            ├─ 决策响应者 (单聊 / 群聊)
            └─ runner_registry → AgentRunner.run()  (起 asyncio.Task, 立即返回)
                 └─ agent_runner.execute_run()
                      ├─ build_adapter_input()  历史注入 + token 预算 + key 选择
                      │   └─ (可选) PromptAssembler 注入 Profile + Recall + Constraints
                      ├─ adapter.stream()  ← L2 (Claude / Codex / Custom / Mock)
                      │     产出 StreamEvent: part.delta / tool.call / artifact.create ...
                      │     工具调用 → tool_registry.execute_with_hooks()
                      │       ├─ pre_tool_use hook (审批/拦截/修改)
                      │       ├─ tool_registry.execute() (沙箱内)
                      │       └─ post_tool_use hook (记忆持久化/技能激活/审计)
                      └─ consume_stream()
                           ├─ persist_event()  事件落 DB
                           │   ├─ Redis 可用: part.delta/tool 等事件 XADD 到 Redis Stream
                           │   │              → DBWriterConsumer 后台批量落 PG (write-behind)
                           │   └─ Redis 不可用: 同步 _update_message_parts 直接落 PG
                           └─ event_bus.publish()  → SSE (零延迟, 不经 Redis)
                                └─ GET /api/stream (一条全局连接)
                                     └─ 前端 stream-provider.tsx onmessage
                                          └─ Zustand store.applyEvent()  → UI 实时更新
```

**编排场景**（统一 Agent Loop）：`execute_run` 根据 `conversation.dispatch_mode` 路由到 `run_agent_loop(mode=...)`：
- **solo**：agent 工具 + `task_dispatch`（depth < MAX 时注入），base prompt + 软自检 + 派发指导
- **coordinated**（Orchestrator）：agent 工具 + `task_dispatch` + `dispatch_plan`，base prompt + 协调者指导；`dispatch_plan` 声明 DAG → `dag_executor` 拓扑排序 + 波调度并行执行 → 可选计划审批
- **subagent**（`task_dispatch` / `dispatch_plan` 触发）：agent 工具 + `task_dispatch`（depth < MAX），base prompt + 子 Agent 指导；clone-self 消息 `hidden=true`

`MAX_DISPATCH_DEPTH = 3`，达到上限时 `task_dispatch` 不注入——该 Agent 为终端执行者。无验证 gate、无重试 harness、无自动重规划——LLM 可根据返回结果自行决定是否重新派发。

---

## 7.5 生命周期 Hooks 数据流

```
Agent 启动 (ON_RUN_START)
  └─ HookRegistry.dispatch(ON_RUN_START)
     └─ 各 hook 按优先级执行 (lower = earlier)

每轮对话循环:
  ├─ PRE_TURN  → auto_compact: 检查是否需要压缩
  │            → checkpoint: 恢复上一轮检查点
  ├─ LLM 调用 + 工具循环:
  │   ├─ PRE_TOOL_USE  → tool_approval: 拦截需审批工具
  │   │                → 可 deny / modify / allow
  │   ├─ tool_registry.execute()
  │   └─ POST_TOOL_USE → audit_log: 记录工具调用
  │                    → memory_persist: 持久化记忆
  │                    → skill_auto_activator: 自动激活技能
  ├─ POST_TURN → checkpoint: 保存当前轮检查点
  │            → summary_generate: 生成摘要
  │            → memory_persist: 固化长期记忆
  └─ ON_MESSAGE_END

运行结束:
  └─ ON_RUN_END / ON_STOP / ON_ERROR

子任务派发 (task_dispatch / dispatch_plan):
  └─ ON_TASK_VERIFIED → 事件保留, verify_stage 已删除 (无内置监听器)
```

**Hook 控制流**：`deny` 立即终止；`modify` 修改参数/结果；`inject` 注入额外事件；`allow` 放行。Agent 通过 `hook_names` 字段启用特定 Hook 组。

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
| `app/` | `layout.tsx` / `page.tsx`（挂载 StreamProvider + AuthGate + 主界面） · `login/page.tsx` · `register/page.tsx` |
| `components/` (75+) | ChatPanel / MessageList / MessageParts / ArtifactPreviewPanel / ArtifactCodeEditor / AgentLibrary / AgentCreateWizard / CreateAgentDialog / DispatchPlanCard / KnowledgeLibrary / DocumentDetail / UploadDocumentDialog / SkillLibrary / GlobalSearch / SettingsDialog / TurnTimeline / MessageHighlightLayer / **AuthGate / ProfileDialog / MemoryLibrary / LongTermMemoryPanel / PreferencePanel / SessionMemoryPanel** ... |
| `lib/` | `api.ts`（REST 客户端，统一 `API_BASE_URL` 前缀）· `config.ts`（读 `NEXT_PUBLIC_API_BASE_URL`）· `api/memory.ts`（记忆管理 API）· `artifact-groups.ts` · `tool-display.ts` · 工具 |
| `stores/` | `app-store.ts`（会话 / 消息 / 事件 reducer）· `search-store.ts` · **`auth-store.ts`**（认证状态 / 用户信息 / token 刷新） |
| `shared/` (15) | StreamEvent / MessagePart / Artifact 等**前后端共享类型**（纯类型，无逻辑） · `codex-compat.ts` · `model-registry.ts` · `ppt-theme.ts` ... |
| `db/schema.ts` | 仅保留前端 import 行类型（AgentRow 等） |

**前后端边界**：前端只通过 `lib/api.ts`（REST）和 `stream-provider.tsx`（SSE EventSource）与 Python 后端通信，两者都加 `API_BASE_URL` 前缀；默认空串 = 同源，设环境变量即指向独立 Python 后端。认证通过 HttpOnly cookie 传递 JWT（同源自动携带），跨域 dev 时 SSE 连接通过 `?token=` query param 认证。

---

## 11. 其它目录

| 目录 | 说明 | 当前状态 |
|---|---|---|
| `specs/` | 19 份编号详细规格（实体 / 事件 / 适配器 / 工具 / 编排 / 统一 Agent Loop ...），**语言无关契约** | 有效 |
| `openspec/` | OpenSpec 能力契约（16 个 capability spec，含 **user-auth** / **user-profile**）+ 变更提案（`changes/` 下 50+ 提案） | 有效 |
| `electron/` | 桌面版（`main.ts` 启动内嵌 Next server） | ⚠️ 待改造：内嵌 Next 已无后端，需改启 Python |
| `apps/mobile/` | 移动伴随 App（Capacitor / 远程审批，spec 14） | 独立模块 |
| `scripts/` | 构建 / Electron / SQLite ABI 辅助（`.mjs`） | 前端用 |
| `skills/` | 可复用开发任务模板（add-adapter / add-tool ...） | 参考 |
| `.agenthub-data/` | 运行时：`workspaces/` + `deployments/` + `skills/` | 前后端共用 |

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
2. **`user_settings.<provider>_api_key`** — 用户在「设置」面板全局自填，存 `user_settings` 表（按 `user_id` 分行）
3. **`backend/.env`** — 环境变量兜底（dev / CI 友好；`config.py` 的 `apply_env_overrides()` 桥接到 `os.environ`）

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
REDIS_URL=                   # 留空 = 禁用 Redis (退化为同步 DB 读写)
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
| Redis | `REDIS_URL` 空 | 退化为同步 DB 读写（无 KV 缓存，无 Stream write-behind） |
| Embedding API | `EMBEDDING_API_KEY` 空 | RAG / LTM 无语义检索能力 |
| LLM API (RAG 用) | 无任何 LLM key | RAG 无 rewrite / rerank；KG 无实体抽取 |

> 启动时后端打印状态面板，一目了然哪些服务已连接、哪些降级。

---

*本文档由整体目录与代码分析生成。深入某子系统请读 `specs/` 对应编号；协作规则见 [CLAUDE.md](./CLAUDE.md)；代码地图见 [OVERVIEW.md](./OVERVIEW.md)。最后更新：2026-07-13 · 同步用户认证与多用户隔离、Redis 元数据缓存 + 异步 DB 写入、DB 表数更新（18→22）、API Key 优先级更新（app_settings → user_settings）。*
