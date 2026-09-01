# AChat 架构与目录说明

> 本文档描述项目的整体架构、目录结构与数据流，反映后端迁移到 Python (FastAPI) 并集成 RAG / 记忆 / 知识图谱 / Document 知识库 / 代码图谱智能 / 执行计划 / Run 内压缩 / Worktree 隔离 / 外部 MCP 接入后的最新状态。
>
> 协作规则见 [CLAUDE.md](./CLAUDE.md)，代码地图见 [OVERVIEW.md](./OVERVIEW.md)，详细契约见 [specs/](./specs/)。

---

## 1. 项目定位

**AChat** 是一个 local-first 的多 Agent 协作平台。一句话：

> 把多 Agent 协作做成 IM 群聊体验。Agent 是「联系人」，对话是「工作空间」，Orchestrator 是「群里的项目经理」。

**核心能力**：

- IM 范式会话管理（单聊 / 群聊 / @提及 / 搜索 / 置顶 / 归档 / 书签）
- 统一适配器层接入 Claude / Codex（CLI 子进程路线）/ Custom(OpenAI 兼容 SDK 路线) / Mock Agent
- Orchestrator 自动拆任务、DAG 并行调度、聚合结果（统一 Agent Loop）
- 产物（代码 / 网页 / 文档 / PPT / 图片）内联预览与二次编辑
- 每会话独立 workspace 沙箱（sandbox / local 双模式）
- **Worktree 隔离**（DAG 波调度并行任务用 git worktree 隔离）
- **用户认证与多用户隔离**（JWT + bcrypt · CSRF 防护 · 所有用户数据 `user_id` 隔离）
- **RAG 混合检索**（Milvus dense vector + 原生 BM25 sparse + Neo4j 知识图谱 PPR + entity/triple 向量召回 + RRF 融合）
- **RAG 文件生命周期**（11 状态状态机 + 异步任务队列 + 乐观并发 + 虚拟目录树）
- **RAG 评测系统**（dataset CRUD + benchmark 自动生成 + LLM-as-Judge + 独立 eval LLM 配置）
- **RAG 分块预设**（general / qa / semantic / separator 四种策略 + 用户级配置）
- **OCR 引擎注册表**（7 种引擎 + auto 模式按优先级自动选择）
- **文件原生记忆系统**（Markdown 文件 + frontmatter + wikilinks + auto_memory/auto_dream pipeline + SQLite FTS5 混合检索）
- **Document + Version 知识库**（全局文档版本化、解析入库、按需召回）
- **Obsidian 知识同步**（vault 同步 + 预处理 + RAG 入库）
- **代码图谱智能**（CodeGraph 本地运行时 + code_explore 工具 + 索引管理）
- **执行计划工具**（create_plan / plan_step / add_plan_steps 结构化计划卡片）
- **全局任务看板**（Task Board · 持久化任务池 · Kanban UI · asyncio 后台调度器自动派发 todo 任务给 Agent）
- **外部 MCP 接入**（MCP Server 配置管理 + client_manager + 调用审批）
- **Run 内压缩**（通用掩码压缩 pipeline：≥ 0.75 掩码旧工具结果 / ≥ 0.88 折叠旧轮次，纯结构化裁剪无 LLM）
- ~~**Redis 元数据缓存 + 异步 DB 写入**~~（**已移除** — 双 DB 架构下 SQLite 直写 + 进程内 dict TTL 缓存替代）
- ~~**Elasticsearch 全文检索**~~（**已移除** — Milvus 原生 BM25 sparse vector 替代）
- **Agent 可观测性与评测系统**（OpenTelemetry 全链路追踪 · Arize Phoenix :6006 · 在线规则评测 · 离线 LLM-as-Judge · 5+4 维评测指标体系）
- **Aeval Agent 评测框架**（独立 PyPI 包 aeval-framework + eval_integration 接入层 + /api/eval 子应用 + 框架内置 Dashboard）
- **DAG 内 Agent 通信**（ask_peer 兄弟会话提问 · report_result 结构化汇报 · AgentSessionRegistry 注册表）
- **会话笔记**（结构化 10 段 YAML Session Note，替代旧无结构摘要）
- 桌面打包（Electron）+ 移动伴随端（Capacitor）

**运行形态**：前后端分离本地运行。前端 Next.js dev server（:3000），后端 FastAPI（:8000）；基础设施服务（PostgreSQL / Milvus / Neo4j / Phoenix）通过 Docker Compose 启动，可全部容器化也可仅远端部署基础设施。

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
| 数据库 | **PostgreSQL 16**（asyncpg 驱动）+ **SQLite**（WAL 模式，本地热数据） |
| 认证 | bcrypt + PyJWT（JWT HttpOnly cookie · `token_version` 全局吊销） |
| AI 适配器 | Claude Code / Codex 走 **CLI 子进程**（stream-json / JSON-RPC 2.0）；Custom 走 `openai` Python SDK |
| 包管理 | pip + venv（`pyproject.toml`） |
| Lint | ruff |
| 测试 | pytest + pytest-asyncio（`asyncio_mode = "auto"`） |
| 可观测性 | OpenTelemetry SDK + Arize Phoenix |

### 基础设施（Docker Compose）

| 服务 | 镜像 | 用途 |
|---|---|---|
| PostgreSQL | `postgres:16-alpine` | 关系型主库（业务库 `agenthub` 27 张表 + Phoenix 专用库 `achat_observability`） |
| Phoenix | `arizephoenix/phoenix:latest` | ★ Agent 可观测性后端（Trace 瀑布流 + Eval 评分 · :6006 Web UI · :4317 OTLP gRPC） |
| Milvus | `milvusdb/milvus:v2.4.17` | 向量检索（RAG dense + sparse BM25 + 图谱 entity/triple 向量） |
| ~~Elasticsearch~~ | ~~`elasticsearch:8.14.0`~~ | ~~全文检索~~ — **已移除**，Milvus 原生 BM25 sparse vector 替代 |
| Neo4j | `neo4j:5-community` | 知识图谱（RAG KGStore · PPR + entity/triple 子图遍历） |
| Kafka | 可选 | 事件总线增强（默认 in-process） |
| Redis | ~~`redis:7-alpine`~~ | ~~元数据缓存 + 异步 DB 写入~~ — **已移除**，双 DB 架构下 SQLite 直写 + 进程内 dict TTL 缓存替代 |

> **降级策略**：每个基础设施服务独立 try/except，单个失败不影响其他。Milvus 挂 → 退化为 TF cosine；Neo4j 挂 → KGStore no-op，RAG 退化为向量+全文；Kafka 不配 → 用 in-process EventBus；Phoenix 不可达 → OTel `BatchSpanProcessor` 缓冲后静默丢弃，不阻断主链路。启动时打印状态面板。

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
│     AgentRunner · AgentLoop · Orchestrator · ConversationService ·   │
│     EventBus · ToolExecutor · RAGService · DocumentService ·         │
│     PromptAssembler · CompactPipeline · WorktreeService ·            │
│     HookRegistry (生命周期 Hooks) ·                                    │
│     Observability (OTel + Phoenix · Level 4 埋点 + 评测)              │
│ L2  Agent Platform Adapters                   backend/app/adapters/ │  ← Python
│     ClaudeCLI · CodexCLI (CLI 子进程) · Custom (SDK) · Mock           │
│ L1  Persistence                               backend/app/db/       │  ← Python
│     SQLAlchemy 双引擎：本地 SQLite[WAL] + 远端 PostgreSQL + workspace FS  │
├──────────────────────────────────────────────────────────────────┤
│  Infrastructure Layer (可选, 独立降级)          backend/app/infra/   │
│  Milvus(向量+BM25+图谱向量) · Neo4j(图谱) · Kafka(事件)     │
│  └─ RAG 混合检索 (backend/app/rag/)  HybridStore + RRF              │
│  └─ 记忆系统 (backend/app/memory/)  file-native + SessionMemory/Preference │
│  └─ 知识图谱 (backend/app/graph/)   KGStore + Extractor             │
│  └─ 代码图谱智能 (backend/app/code_intelligence/)  CodeGraph 运行时  │
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
├── backend/              ★ Python 后端 (L1-L3 + 适配器 + RAG + 记忆 + 图谱 + 代码图谱) —— 全部业务逻辑
├── src/                  前端 (L4-L5) + 共享类型
│   ├── app/              Next.js 页面 (layout / page)
│   ├── components/       80+ React 组件 (不含 ui/ 和 test)
│   ├── lib/              api.ts (REST 客户端) · config.ts (API base) · 工具
│   ├── stores/           Zustand store (app-store / search-store / auth-store)
│   ├── shared/           ★ 共享类型 (StreamEvent / MessagePart ...) 前后端契约源
│   └── db/schema.ts      仅保留前端 import 行类型 (DB 实体由后端 SQLAlchemy 拥有)
├── electron/             桌面版外壳 (main.ts / paths.ts / server-bootstrap.ts)
├── apps/mobile/          移动伴随 App (Capacitor)
├── packages/shared/      共享包 (workspace)
├── specs/                ★ 20 份编号详细规格 (语言无关契约)
├── openspec/             OpenSpec 能力契约 (19 个 capability spec) + 变更提案
├── skills/               可复用开发任务模板
├── scripts/              构建 / Electron / SQLite 辅助脚本 (.mjs)
├── docs/                 文档 + 图片
├── .agenthub-data/       运行时数据 (workspaces + deployments + skills + worktrees)
├── docker-compose.yml            全栈容器化 (前后端 + 基基础设施)
├── docker-compose.infra.yml      仅基础设施 (本机跑前后端, 远端跑 PG/Milvus/Neo4j/Phoenix)
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
│   │                         → PromptAssembler → DocumentService → 状态面板
│   │                         → EVAL_HARNESS_ENABLED 时 /api/eval 挂载 Aeval 子应用)
│   ├── config.py           配置 (pydantic-settings) + .env key 桥接到 os.environ
│   │
│   ├── auth/ (5)            【认证模块】
│   │   ├── jwt_handler.py    JWT 生成/验证 (access 1h + refresh 7d)
│   │   ├── password.py       bcrypt 密码哈希 (cost factor 12)
│   │   ├── service.py        认证业务逻辑 (注册/登录/刷新/登出/VIP 快捷登录)
│   │   ├── dependencies.py   FastAPI 依赖注入 (获取当前用户 · token_version 校验)
│   │   └── ownership.py      资源所有权检查 (user_id 隔离)
│   │
│   ├── db/ (5)             【L1 持久化】
│   │   ├── models.py        27 张表 SQLAlchemy 模型 (核心域 + ModelProfile + AGI-memory + Document + Task Board + RAG Task Queue + RAG Eval)
│   │   ├── table_routing.py ★ 双 DB 表路由 (14 张本地 SQLite + 13 张远端 PG)
│   │   ├── engine.py        ★ 双引擎: 本地 SQLite[WAL] + 远端 PostgreSQL (连接池)
│   │   ├── migrations/      ★ schema 迁移脚本 (rag_overhaul + user_settings_rag_config)
│   │   └── __init__.py      模块导出
│   │
│   ├── schemas/ (10)       【类型契约 Pydantic】
│   │   ├── events.py        30+ StreamEvent (SSE 协议, snake_case + camelCase 别名)
│   │   ├── messages.py      MessagePart (parts 数组)
│   │   ├── artifacts.py     Artifact 内容类型
│   │   ├── dispatch.py      调度计划 / 任务
│   │   ├── document.py      Document / DocumentVersion
│   │   ├── plan.py          ★ 执行计划 (PlanStep / PlanState / PlanComplexity)
│   │   ├── obsidian.py      ★ Obsidian 同步配置
│   │   ├── model_profile.py ★ ModelProfile 配置
│   │   ├── task.py          ★ Task Board (CreateTask / MoveTask / SchedulerStart ...)
│   │   └── requests.py      API 请求 / 响应模型
│   │
│   ├── services/ (42+)     【L3 业务逻辑 —— 核心大头】
│   │   ├── agent_runner.py        ★ 执行器 (execute_run 路由 + execute_simple_run ReAct loop
│   │   │                          + baseline 工具合并 + build_adapter_input)
│   │   ├── agent_loop.py          ★ 统一 Agent Loop (run_agent_loop: solo/coordinated/subagent)
│   │   │                          spawn_subagent_loop (递归子 Agent 派发) + prompt builders
│   │   ├── dag_executor.py        ★ DAG 验证 / 波调度 / 并行执行 (validate_dag / topological_waves / execute_dag)
│   │   ├── agent_session_registry.py ★ DAG 会话注册表 (task_id → AgentSession · ask_peer 查兄弟会话 · 父邮箱 · 300s TTL)
│   │   ├── worktree_service.py    ★ git worktree 隔离 (DAG 波调度并行任务 · 创建→merge-back→清理 · 非 git 目录拷贝降级 · 三层冲突解决: Auto → LLM → Human)
│   │   ├── pending_merge_conflicts.py ★ Worktree merge 冲突人工审批 store
│   │   ├── workspace_env_service.py ★ workspace 环境变量隔离
│   │   ├── compact_pipeline.py    ★ Run 内压缩通用掩码 pipeline (stage 1 ratio ≥ 0.75 掩码旧工具结果 · stage 3 ≥ 0.88 折叠旧轮次 · 纯结构化裁剪)
│   │   ├── compact_markers.py     压缩标记构建 (CompactMarkerBuilder / CompactSuccessJudge)
│   │   ├── react_loop_termination.py ★ ReAct loop 终止逻辑 (stage 4 软收尾 + stage 5 强制终止)
│   │   ├── transcript_renderer.py ★ 统一消息流渲染逻辑
│   │   ├── guide_prompt.py        ★ 小A Guide Agent system prompt (管理边界/确认规则/记忆整理规则)
│   │   ├── orchestrator.py        stub (旧三阶段已移除, 仅保留壳)
│   │   ├── orchestrator_prompts.py工具函数 (extract_text_from_parts 等)
│   │   ├── conversation_service.py会话 / 消息全生命周期
│   │   ├── event_bus.py           SSE 事件总线 (asyncio.Queue 扇出)
│   │   ├── conversation_context.py跨 run 历史注入 (hidden 消息过滤)
│   │   ├── artifact_service.py    产物 CRUD / 版本链
│   │   ├── deployment_service.py  产物部署 + 资源 / zip
│   │   ├── settings_service.py    全局设置 / API key 解析
│   │   ├── global_settings_service.py 全局设置缓存 (进程内 dict TTL)
│   │   ├── async_db_writer.py     ★ 已移除 (Redis Stream write-behind 废弃，改为直写 SQLite)
│   │   ├── recovery_scan.py       ★ 启动崩溃恢复 (SQLite WAL 自带崩溃恢复)
│   │   ├── fs_service.py          workspace 文件读写 + 沙箱配额
│   │   ├── search_service.py      消息全文搜索
│   │   ├── rag_service.py         ★ RAG 混合检索 (Milvus dense+sparse + KG + RRF)
│   │   ├── document_service.py    ★ Document + Version 知识库 CRUD
│   │   ├── obsidian_sync_service.py ★ Obsidian vault 同步
│   │   ├── prompt_assembler.py    ★ 上下文组装 (Profile + Recall + Constraints)
│   │   ├── skill_service.py       Agent Skills 加载 / 写入
│   │   ├── runner_registry.py     per-conversation runner 生命周期
│   │   ├── deploy_command_service.py 部署斜杠命令
│   │   ├── context_compaction_service.py 上下文压缩 (跨 run)
│   │   ├── usage_summary_service.py Token 分析聚合
│   │   ├── checkpoint_service.py  SDK Agent turn 级检查点保存/恢复
│   │   ├── hook_registry.py       ★ 生命周期 Hook 注册与分发
│   │   ├── plan_registry.py       ★ 执行计划注册 / 查询
│   │   ├── plan_dispatch_mapping.py ★ 计划→派发映射
│   │   ├── plan_usage_service.py  ★ 计划用量统计
│   │   ├── project_artifact.py    项目产物管理
│   │   ├── agent_load_tracker.py  Agent 负载追踪
│   │   ├── task_service.py       ★ Task CRUD + 乐观并发控制 (version 列)
│   │   ├── task_scheduler.py     ★ asyncio 后台调度器 (定期扫描 todo → dispatch 给 Agent)
│   │   ├── network_hints.py       移动端网络发现
│   │   ├── bash_command_approval.py bash 命令审批逻辑
│   │   ├── hooks/                 ★ 内置 Hook 实现 (7 个)
│   │   │   ├── audit_log.py       审计日志
│   │   │   ├── auto_compact.py    自动上下文压缩
│   │   │   ├── checkpoint.py      检查点保存
│   │   │   ├── memory_persist.py  记忆持久化
│   │   │   ├── skill_auto_activator.py 技能自动激活
│   │   │   ├── summary_generate.py 摘要生成
│   │   │   └── tool_approval.py   工具审批拦截
│   │   └── pending_*.py           审批 / 提问 / 命令 / 计划 / MCP 内存 store
│   │
│   ├── adapters/ (11)      【L2 适配器】stream(input, cancel_event) -> AsyncIterator[StreamEvent]
│   │   ├── base.py          AdapterInput + ABC + AdapterName (事件流契约)
│   │   ├── cli_base.py      ★ CLI 适配器公共基类 (子进程生命周期 / 管道 / 超时取消 / 参数过滤)
│   │   ├── conpty.py        Windows ConPTY 支持 (隐藏窗口 / 伪终端)
│   │   ├── _delta_flusher.py ★ 增量刷新器 (流式 delta 批量刷新)
│   │   ├── claude_adapter.py ★ ClaudeCLIAdapter: spawn `claude` stream-json 协议
│   │   ├── codex_adapter.py  ★ CodexCLIAdapter: spawn `codex app-server` JSON-RPC 2.0
│   │   ├── mock_adapter.py  Mock (脚本流, 不烧 token)
│   │   ├── custom_adapter.py OpenAI 兼容 (DeepSeek / 火山方舟等, SDK 路线, model-done 主路径)
│   │   └── custom_provider_client.py / registry.py / session_store.py
│   │
│   ├── auth/ (5)           【认证模块】见上方
│   │
│   ├── code_intelligence/ (10) 【代码图谱智能】★
│   │   ├── runtime.py        CodeGraph 运行时管理 (下载/解析/版本匹配)
│   │   ├── index_manager.py  索引管理 (启用/同步/重建)
│   │   ├── service.py        后台编排 (异步任务 + 防抖同步)
│   │   ├── process_runner.py CodeGraph 命令执行器
│   │   ├── state_machine.py  索引状态机 (状态转换约束)
│   │   ├── bootstrap.py      启动初始化
│   │   ├── debounce.py       ReadySync 防抖器
│   │   ├── metadata.py       元数据存储 (符号计数等)
│   │   └── progress.py       进度回调
│   │
│   ├── mcp/ (1)            【MCP 客户端】
│   │   └── client_manager.py ★ 外部 MCP Server 连接管理 (stdio/SSE 传输 · 工具发现 · 调用代理)
│   │
│   ├── mcp_bridge.py      ★ AChat MCP Bridge: stdio MCP Server, 把 write_artifact/ask_user/task_dispatch 等平台工具暴露给 CLI agent
│   │
│   ├── tools/ (37)         【工具系统】47 个注册工具
│   │   ├── base.py / registry.py  ToolContext (asyncio.Event 取消) + 注册表
│   │   ├── write_artifact / read_artifact / update_artifact (★ 增量更新)
│   │   ├── deploy_artifact / deploy_workspace
│   │   ├── read_attachment (PDF: pypdf)
│   │   ├── fs_read / fs_write / fs_edit / fs_list / fs_glob / fs_grep / bash (黑名单 + 审批)
│   │   ├── code_explore (★ 代码图谱探索)
│   │   ├── task_dispatch (子 Agent 克隆派发) / dispatch_plan (DAG 派发)
│   │   ├── execution_plan (★ create_plan / plan_step / add_plan_steps 执行计划)
│   │   ├── ask_user
│   │   ├── ask_peer (★ DAG 内兄弟会话提问) / report_result (★ 子 Agent 结构化汇报, terminal tool)
│   │   ├── web_search (Tavily API)
│   │   ├── memory_rag (memory_recall + rag_search/ingest/list/delete)
│   │   ├── memory_store (★ 主动记忆存储，支持结构化字段 summary/keywords/content_scope)
│   │   ├── memory_proactive (★ 主动记忆拉取)
│   │   ├── skills (load_skill / write_skill)
│   │   ├── ★ manage_base (管理工具公共基类)
│   │   ├── ★ manage_agents / manage_skills / manage_mcp / manage_documents
│   │   │  / manage_memory / manage_profile / manage_conversations / manage_tasks
│   │   │  (8 个 guide agent 专用管理工具, 仅对 is_guide=True 的 Agent 注入)
│   │   ├── ★ task_tools (7 个 task 工具: task_list/get/create/claim/complete/move/comment — 仅 TaskScheduler 派发的运行注入)
│   │   └── rate_limiter.py
│   │
│   ├── rag/                【RAG 引擎】★ 大重构
│   │   ├── rag_engine.py    HybridStore: Milvus dense (COSINE) + Milvus sparse BM25 + Neo4j KG (PPR + entity/triple vector) + RRF
│   │   ├── parser.py        文档解析 (pdfplumber → PyPDF2 → pdftotext 三级降级) + OCR dispatch
│   │   ├── parser_registry.py ★ OCR 引擎注册表 (7 种引擎 lazy import + auto 优先级)
│   │   ├── parsers/          ★ OCR 引擎实现 (base.py + rapid_ocr/mineru/mineru_official/pp_structure_v3/deepseek_ocr/paddleocr_api/unified)
│   │   ├── splitter.py      文档分块 (chunk_size / overlap)
│   │   ├── chunking/        ★ 分块预设 (presets.py 4 种策略 + dispatcher.py 路由 + nlp.py 分词 + parsers/ + utils/)
│   │   ├── file_lifecycle.py ★ 文件生命周期状态机 (11 状态 + 乐观并发 + Document.status + graph_status)
│   │   ├── graph_build_task.py ★ 异步图谱构建 (分批 extract → 并发 Neo4j MERGE + 重试)
│   │   ├── graph_retrieval.py ★ 图谱检索增强 (PPR + entity/triple vector search)
│   │   ├── milvus_graph_vector_store.py ★ 图谱 entity/triple Milvus 向量存储
│   │   ├── eval/            ★ RAG 评测模块 (service.py + evaluator.py + metrics.py + benchmark_generation.py)
│   │   ├── reranker.py      Reranking (LLM 打分重排)
│   │   └── obsidian_preprocessor.py ★ Obsidian vault 预处理 (wikilink 解析 · frontmatter 提取)
│   │   ─── rewriter.py 已移除 (Query Rewriting 删除) ───
│   │
│   ├── memory/             【文件原生记忆系统】
│   │   ├── memory_service.py  ★ 门面: file-native pipeline + Preference + SessionMemory
│   │   ├── file_store/        Markdown 文件读写 + frontmatter + wikilinks + workspace
│   │   ├── search/            SQLite FTS5 BM25 + wikilink 图扩展 + RRF 融合
│   │   ├── pipeline/          auto_memory + auto_index + auto_dream + proactive
│   │   ├── preference.py      用户偏好 (user_preferences 表, KV) — 保留不动
│   │   ├── session_memory.py  会话摘要 (跨 run 上下文压缩) — 保留不动
│   │   ├── session_note.py    ★ 结构化 10 段 YAML 会话笔记 (YAML 存储 / XML 注入 / 解析失败回退纯文本)
│   │   └── memory_writer_compat.py  Preference 提取工具 (从旧 memory_writer 保留)
│   │
│   ├── graph/ (5)          【知识图谱】
│   │   ├── kgstore.py       KGStore: 文档 → 实体/关系抽取 → Neo4j 入图 → 子图检索
│   │   ├── extractors/      ★ 模块化抽取器 (base.py ABC + factory.py 注册工厂 + llm.py LLM 批量抽取)
│   │   ├── graph_utils.py   ★ 图谱工具函数
│   │   ├── extractor.py     LLM 驱动的实体 / 关系抽取 (旧入口, 保留兼容)
│   │   └── types.py         图谱类型定义
│   │                        可视化端点在 api/graph.py (stats / subgraph / labels)
│   │
│   ├── infra/ (6)          【基础设施工厂】
│   │   ├── factory.py       build_infrastructure(): 配置驱动, 独立降级
│   │   │                   (Milvus/Neo4j/Kafka — Redis/ES 已移除)
│   │   ├── hybrid.py        HybridStore 抽象 (向量 + 全文 + 图谱统一接口)
│   │   ├── cache.py         ★ 进程内 dict TTL 缓存 (替代 Redis KV，已移除)
│   │   ├── cache_helpers.py ★ 缓存实体查找 (Agent/Workspace 本地 SQLite 直读; UserSettings/GlobalSettings 远端 PG + dict TTL)
│   │   ├── cache_metrics.py 嵌入缓存命中率指标
│   │   └── status.py        基础设施连接状态面板 + 可观测性状态
│   │
│   ├── api/ (28)           【API 路由】
│   │   ├── conversations / messages / agents / artifacts / attachments
│   │   ├── fs / pending / settings / runs_misc / stream (SSE)
│   │   ├── documents / skills / deployments / **auth** / **eval**
│   │   ├── **code_intelligence** / **mcp** / **memory** / **obsidian**
│   │   ├── **plan_usage** / **profile** / **workspaces**
│   │   ├── **model_profiles** / **tasks**
│   │   ├── **rag_config** / **rag_eval** / **rag_tasks**  ★ RAG 配置/评测/任务队列
│   │   └── mobile/routes
│   │
│   ├── observability/ (7)   【Agent 可观测性与评测】★ OTel + Phoenix
│   │   ├── tracer.py          OTel TracerProvider 生命周期 (BatchSpanProcessor + OTLP → Phoenix)
│   │   ├── instrumentation.py @traced 装饰器 + 属性 key 常量 (agenthub.* 前缀)
│   │   ├── span_names.py      中英文 span name 映射表 (agent.run · 代理运行)
│   │   ├── run_collector.py   ★ Per-run 内存 span 收集器 (在线规则评测用，解决 OTel 异步发送的竞态)
│   │   ├── eval_rules.py      在线规则评测 (14 指标：任务完成率/工具成功率/轮次效率/...)
│   │   ├── eval_judge.py      离线 LLM-as-Judge (9 维度：工具选择/子任务粒度/聚合忠实度/...)
│   │   └── eval_metrics.py    评测指标体系 (Agent 全过程 5 维度 + 多 Agent 协作 4 维度)
│   │
│   ├── eval_integration/ (7) 【Aeval 评测接入层】★ 框架本体在 PyPI 包 aeval-framework
│   │   ├── config.py          create_aeval_runner() 装配入口 (缺凭证报 EvalConfigError)
│   │   ├── runner.py          AChatAgentRunner + WorkspaceCoordinator (评测任务 → agent 运行)
│   │   ├── environment.py     AChatWorkspaceEnvironment (评测 workspace 隔离)
│   │   ├── graders/           Artifact / Dispatch grader
│   │   ├── client.py          AChatApiClient + Bearer token provider
│   │   ├── trace_bridge.py    进程内 RunTraceBridge (AChat trace ↔ 评测 trial trace_id)
│   │   └── errors.py          EvalConfigError 等异常
│   │                          EVAL_HARNESS_ENABLED=true 时 main.py 在 /api/eval 挂载
│   │                          agent_eval 子应用; 套件 YAML 在 backend/eval_suites/
│   │
│   └── utils/ (14)         跨平台 · 安全黑名单 · ID · token 估算 · 审批 helper · mermaid 规范化 ...
│
└── tests/ (178)           pytest 测试; ruff 全绿
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

## 6. 数据库：27 张表

### 用户域（1 张）

| 表 | 说明 | 路由 |
|---|---|---|
| `users` | 用户（username / email / password_hash / token_version / display_name / avatar） | 远端 PG |

### 核心域（10 张）

| 表 | 说明 | 路由 |
|---|---|---|
| `agents` | AI 代理（name / adapter_name / system_prompt / tool_names / skill_names / hook_names / api_key / executable_path / protocol_family / custom_args / **user_id** / **is_guide**）★ `is_guide=True` 标记 guide agent，跳过 baseline 工具合并，仅注入管理工具 | 本地 SQLite |
| `conversations` | 会话（mode single/group/**guide** / agent_ids / pinned / bookmarked / archived / rag_enabled / summary / dispatch_mode / **user_id**）★ `mode='guide'` 会话不出现在列表/搜索/不可删 | 本地 SQLite |
| `messages` | 消息（role / parts JSON / status / run_id / usage / hidden） | 本地 SQLite |
| `artifacts` | 产物（type / content JSON / version / parent_artifact_id） | 本地 SQLite |
| `workspaces` | 工作区（mode sandbox/local / root_path / bound_path） | 本地 SQLite |
| `attachments` | 附件（kind image/file / file_path / mime_type） | 本地 SQLite |
| `agent_runs` | 运行记录（status / usage / dispatch_plan / dispatch_results / parent_run_id） | 本地 SQLite |
| `agent_run_checkpoints` | SDK Agent turn 级检查点（run_id / turn_number / messages_json） | 本地 SQLite |
| `conversation_context_summaries` | 上下文压缩摘要 | 本地 SQLite |
| `app_settings` | 全局设置单行表（各 provider API key + 部署配置 + companion） | 远端 PG |

### 设置域（4 张）

| 表 | 说明 | 路由 |
|---|---|---|
| `global_settings` | 全局部署配置（deployment_publish_enabled / deployment_publish_dir / deployment_public_base_url） | 远端 PG |
| `user_settings` | 用户级设置（user_id / 各 provider API key / companion_mode / mobile_device_token / **rag_chunk_preset** / **rag_chunk_size** / **rag_chunk_overlap** / **ocr_engine**）★ 新增 RAG 配置字段 | 远端 PG |
| `mcp_servers` | MCP Server 配置（user_id / name / command / args / env / transport_type） | 本地 SQLite |
| `model_profiles` | ★ ModelProfile 用户级模型配置（user_id / provider / model_id / api_key / api_base_url / supports_vision / is_default） | 本地 SQLite |

### AGI-memory 新增（6 张）

| 表 | 说明 | 路由 |
|---|---|---|
| `user_preferences` | 用户偏好 KV（**user_id** / key / value / source） | 远端 PG |
| `rag_chunks` | RAG 文档分块（doc_hash / chunk_idx / content / embedding / document_id / version_id / content_hash / **user_id** / **chunk_token_count** / **start_char_pos** / **end_char_pos**）★ 新增分块位置字段 | 远端 PG |
| `chat_history` | 对话历史持久化（role / content） | 远端 PG |

### Document + Version 知识库（2 张）

| 表 | 说明 | 路由 |
|---|---|---|
| `documents` | 全局知识库文档（title / doc_type / source / status / latest_version_id / **chunk_preset** / **graph_status** / **parent_id** / **is_folder**）★ 新增分块预设/图谱状态/虚拟目录树字段 | 远端 PG |
| `document_versions` | 文档版本（document_id / version / content_md / summary / metadata） | 远端 PG |

### Task Board（2 张）★

| 表 | 说明 | 路由 |
|---|---|---|
| `tasks` | 全局任务池（id / user_id / title / description / status / priority / labels / assignee_agent_id / creator_type / conversation_id / workspace_mode / version 乐观并发 / failure_count / sort_order / due_date） | 本地 SQLite |
| `task_comments` | 任务评论（task_id / user_id / body / author_type / author_id / author_name / version / created_at） | 本地 SQLite |

### RAG Task Queue（1 张）★

| 表 | 说明 | 路由 |
|---|---|---|
| `rag_tasks` | RAG 生命周期任务（id / user_id / task_type: parse\|ingest\|graph_build\|delete_cleanup / document_id / version_id / status: pending\|running\|completed\|failed\|failed_permanent / payload / result / error_message / retry_count / max_retries / timestamps）独立于 Task Board `tasks` 表 | 本地 SQLite |

### RAG Evaluation（4 张）★

| 表 | 说明 | 路由 |
|---|---|---|
| `eval_datasets` | 评测数据集元数据（user_id / name / description / item_count / has_gold_chunks / has_gold_answers / build_metadata） | 远端 PG |
| `eval_dataset_items` | 评测数据集条目（dataset_id / item_index / query_text / gold_chunk_ids / gold_answer） | 远端 PG |
| `eval_runs` | 评测运行（user_id / dataset_id / status / retrieval_config / metrics / overall_score / total_items / completed_items） | 远端 PG |
| `eval_run_items` | 评测运行条目结果（run_id / item_index / dataset_item_id / query_text / gold_chunk_ids / gold_answer / generated_answer / retrieved_chunks / metrics） | 远端 PG |

---

## 7. 一条消息的生命周期（数据流）

```
用户在 UI 输入并发送
  └─ src/lib/api.ts  POST /api/conversations/{id}/messages (JWT cookie 认证 + Origin CSRF 检查)
       └─ L3 conversation_service.send_message()
            ├─ 持久化用户 message
            ├─ 决策响应者 (单聊 / 群聊)
            └─ runner_registry → AgentRunner.run()  (起 asyncio.Task, 立即返回)
                 └─ agent_runner.execute_run()  ← 〔OTel Span: agent.run · 代理运行〕
                      ├─ build_adapter_input()  ← 〔OTel Span: agent.build_context · 上下文组装〕
                      │   ├─ (SDK agent) baseline 工具合并: BASELINE_AGENT_TOOLS + tool_names + 自动注入
                      │   └─ (可选) PromptAssembler 注入 Profile + Recall + Constraints
                      │       ← 〔OTel Span: prompt.assemble · 提示词组装〕
                      ├─ adapter.stream()  ← 〔OTel Span: adapter.stream · 模型推理〕 ← L2
                      │     产出 StreamEvent: part.delta / tool.call / artifact.create ...
                      │     每轮 LLM 生成 ← 〔OTel Span: llm.generate · LLM生成 (第N轮)〕
                      │     工具调用 → tool_registry.execute_with_hooks()
                      │       ← 〔OTel Span: tool.call · 工具调用〕
                      │       ├─ pre_tool_use hook (审批/拦截/修改)
                      │       ├─ tool_registry.execute() (沙箱内)
                      │       └─ post_tool_use hook (记忆持久化/技能激活/审计)
                      │    子 Agent 派发 ← 〔OTel Span: tool.dispatch · 任务派发 → 嵌套 agent.run〕
                      │    Run 内压缩 ← compact_pipeline (ratio ≥ 阈值时触发掩码/折叠裁剪)
                      └─ consume_stream()  ← 〔OTel Span: agent.finalize · 运行收尾〕
                     ├─ persist_event()  事件落 DB (直写本地 SQLite)
                     │   └─ 双 DB 架构: 对话热数据 → 本地 SQLite[WAL] (<1ms)
                     │      用户/知识数据 → 远端 PostgreSQL (50ms)
                     └─ event_bus.publish()  → SSE (零延迟)
                                └─ GET /api/stream (一条全局连接)
                                     └─ 前端 stream-provider.tsx onmessage
                                          └─ Zustand store.applyEvent()  → UI 实时更新
```

**编排场景**（统一 Agent Loop）：`execute_run` 根据 `conversation.dispatch_mode` 路由到 `run_agent_loop(mode=...)`：
- **solo**：agent 工具 + `task_dispatch`（depth < MAX 时注入），base prompt + 软自检 + 派发指导
- **coordinated**（Orchestrator）：agent 工具 + `task_dispatch` + `dispatch_plan`，base prompt + 协调者指导；`dispatch_plan` 声明 DAG → `dag_executor` 拓扑排序 + 波调度并行执行 → 可选计划审批；波调度并行任务可用 `worktree_service` 隔离
- **subagent**（`task_dispatch` / `dispatch_plan` 触发）：agent 工具 + `task_dispatch`（depth < MAX），base prompt + 子 Agent 指导；clone-self 消息 `hidden=true`

`MAX_DISPATCH_DEPTH = 3`，达到上限时 `task_dispatch` 不注入——该 Agent 为终端执行者。无验证 gate、无重试 harness、无自动重规划——LLM 可根据返回结果自行决定是否重新派发。

**★ Guide Agent（小A）双活跃会话模型**：

```
用户登录
  └─ 前端 useEffect 检查 guideConversationId 为空
     └─ POST /api/conversations {mode:'guide', agentIds:['ag_guide_builtin']}
        └─ 后端 create_conversation: 跳过 agent 数量校验, 创建空 sandbox workspace
        └─ 返回 guideConversationId, 前端展开 GuideFloatingPanel

工作会话 (activeConversationId)         Guide 会话 (guideConversationId)
  ├─ 主聊天面板                              ├─ GuideFloatingPanel 悬浮组件
  ├─ 完整 MessageList + MessageInput         ├─ 精简 MessageList (text + tool_use + ask_user)
  ├─ 附件 / 斜杠命令 / @mention              ├─ 无附件 / 无斜杠命令 / 固定 mentionedAgentIds
  └─ 完整工具集 (baseline + optional)        └─ 8 个管理工具 + ask_user (无 baseline)

小A 管理操作副作用:
  └─ manage_* 工具执行成功
     └─ EventBus 发送 guide_side_effect 事件 (target + action + user_id)
        └─ SSE 推到前端
           └─ app-store reducer 按 target 触发对应面板刷新标志
              ├─ target=agents      → fetchAgents()
              ├─ target=skills      → fetchSkills()
              ├─ target=mcp         → fetchMcpServers()
              ├─ target=documents   → fetchDocuments()
              ├─ target=memory      → fetchMemories()
              ├─ target=profile     → fetchProfile() / fetchSettings()
              └─ target=conversations → fetchConversations()
```

小A 走 custom adapter SDK 路线 + `run_agent_loop(mode='solo')`，无新 adapter、无独立服务路径。`is_guide=True` 跳过 baseline 合并；非 guide agent 即使 `tool_names` 误配管理工具也会被过滤。`mode='guide'` 会话不出现在 `list_conversations`、不可删除、不出现在全局搜索。开箱即用：`GUIDE_AGENT_*` 环境变量配置（默认 deepseek provider，走 `DEEPSEEK_API_KEY` 三层 key 链兜底）。

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
文件生命周期 + 任务队列:
  upload_file → FileLifecycleManager (status=uploading → parsing)
    → RagTaskWorker polls pending rag_tasks
      ├─ parse: parser_registry → OCR 引擎 dispatch → 解析为 Markdown
      │   → splitter + chunking preset → 分块
      ├─ ingest: embedding API → 向量
      │   ├─ rag_chunks (PG, content_hash 缓存)
      │   ├─ Milvus dense insert (COSINE + IVF_FLAT)
      │   └─ Milvus sparse insert (BM25 + SPARSE_INVERTED_INDEX)
      ├─ graph_build: GraphBuildTask (异步, fire-and-forget)
      │   └─ 分批 extract → 并发 Neo4j MERGE → graph_status 流转
      │       └─ MilvusGraphVectorStore insert (entity/triple 向量)
      └─ delete_cleanup: 级联删除 chunks + Milvus + Neo4j 数据

查询召回:
  user query
    └─ 并行检索:
       ├─ Milvus dense search (语义相似度, COSINE)
       ├─ Milvus sparse search (全文匹配, BM25)
       └─ GraphRetrieval.search (图谱增强)
           ├─ MilvusGraphVectorStore → entity/triple 向量召回
           └─ KGStore.search_with_ppr (Neo4j PPR 扩散)
    └─ RRF 融合 (三路加权)
       └─ (可选) reranker.py LLM 重排
          └─ 返回 top_k chunks → 注入 Agent 上下文

状态机:
  Document.status: uploading → parsing → parsed → indexing → indexed → active
  Document.graph_status: graph_pending → graph_building → graph_indexed
  乐观并发: UPDATE ... WHERE id=? AND status=? (非法转换被拒绝)
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
└─ 写入 daily/ Markdown 卡片 (auto_memory pipeline)
└─ auto_dream 精炼: daily → digest/{procedure,wiki} Markdown 文件

Agent 运行时注入 (PromptAssembler):
  ProfileSource (UserPreference)  → 用户偏好
  RecallSource (file-native hybrid search) → BM25 + Vector cosine 召回相关记忆
  ConstraintsSource               → 约束规则
  → 组装为 system prompt 补充段
```

---

## 10. Run 内压缩数据流

```
SDK ReAct loop 每轮迭代后:
  └─ 估算 token 占用 ratio = current_tokens / context_window
     ├─ ratio < 0.75  → 无操作
     ├─ ratio ≥ 0.75  → Stage 1: 通用掩码旧 tool 结果 (结构化摘要替代原始输出)
     ├─ ratio ≥ 0.88  → Stage 3: 将更旧轮次折叠为单个 marker
     ├─ ratio ≥ 0.93  → Stage 4: 软收尾注入 (react_loop_termination)
     └─ ratio ≥ 0.95  → Stage 5: 强制终止 (react_loop_termination)

特点:
  - 原 Stage 2 已并入 Stage 1 (掩码后无需单独重裁), 路由层 stage in (1, 2) 同走掩码
  - Stage 1/3 纯结构化裁剪, 无 LLM 调用
  - Token 估算只算 content + tool_calls.function.name/arguments + reasoning_content
  - 基于 CompactMessage 统一抽象, Run 内压缩与 Layer 3 跨 run 压缩共用折叠逻辑
  - 独立于跨 run 的 conversation-context 压缩 (Tier 1) 和 LLM 全量压缩 (Tier 2/3)
```

---

## 11. 前端结构 (`src/`)

| 目录 | 内容 |
|---|---|
| `app/` | `layout.tsx` / `page.tsx`（挂载 StreamProvider + AuthGate + 主界面） · `login/page.tsx` · `register/page.tsx` |
| `components/` (90+) | ChatPanel / MessageList / MessageParts / ArtifactPreviewPanel / ArtifactCodeEditor / AgentLibrary / AgentCreateWizard / CreateAgentDialog / DispatchPlanCard / KnowledgeLibrary / DocumentDetail / UploadDocumentDialog / SkillLibrary / GlobalSearch / SettingsDialog / TurnTimeline / MessageHighlightLayer / WaveColumnHeader / **AuthGate / LoginDialog / AuthLogo / ProfileDialog / MemoryLibrary / CodeIntelligenceControl / McpServerLibrary / PendingMcpCallCard / WorkspaceEnvHintCard / DiffBlock / MergeConflictPanel / DispatchDagGraph / ModelConfigTab** · ★ **TaskBoardView / TaskBoardCard / TaskBoardColumn / TaskBoardDetail / TaskBoardEditor / TaskDetailPanel** (任务看板) · 6 个 sidebar-nav 组件 (Agent/Artifact/Task/Resources/Cognition/Extension) ... |
| `lib/` | `api.ts`（REST 客户端，统一 `API_BASE_URL` 前缀）· `config.ts`（读 `NEXT_PUBLIC_API_BASE_URL`）· `api/memory.ts`（记忆管理 API）· `code-intelligence.ts`（代码图谱 API）· `artifact-groups.ts` · `tool-display.ts` · `wave-utils.ts` · `use-elapsed-timer.ts`（耗时 UI） · 工具 |
| `stores/` | `app-store.ts`（会话 / 消息 / 事件 reducer）· `search-store.ts` · **`auth-store.ts`**（认证状态 / 用户信息 / token 刷新） |
| `shared/` (18) | StreamEvent / MessagePart / Artifact 等**前后端共享类型**（纯类型，无逻辑） · `agent-builder-config.ts`（★ 4 角色预设 + baseline 工具配置） · `codex-compat.ts` · `model-registry.ts` · `ppt-theme.ts` · `usage.ts` ... |
| `db/schema.ts` | 仅保留前端 import 行类型（AgentRow 等） |

**前后端边界**：前端只通过 `lib/api.ts`（REST）和 `stream-provider.tsx`（SSE EventSource）与 Python 后端通信，两者都加 `API_BASE_URL` 前缀；默认空串 = 同源，设环境变量即指向独立 Python 后端。认证通过 HttpOnly cookie 传递 JWT（同源自动携带），跨域 dev 时 SSE 连接通过 `?token=` query param 认证。

---

## 12. 其它目录

| 目录 | 说明 | 当前状态 |
|---|---|---|
| `specs/` | 20 份编号详细规格（实体 / 事件 / 适配器 / 工具 / 编排 / 统一 Agent Loop ...），**语言无关契约** | 有效 |
| `openspec/` | OpenSpec 能力契约（19 个 capability spec，含 **user-auth** / **run-internal-compaction** / **worktree-conflict-resolution**）+ 变更提案（`changes/` 下 100+ 提案，含 RAG 大重构 12 个 change） | 有效 |
| `electron/` | 桌面版（`main.ts` 启动内嵌 Next server） | ⚠️ 待改造：内嵌 Next 已无后端，需改启 Python |
| `apps/mobile/` | 移动伴随 App（Capacitor / 远程审批，spec 14） | 独立模块 |
| `scripts/` | 构建 / Electron / SQLite ABI 辅助（`.mjs`） | 前端用 |
| `skills/` | 可复用开发任务模板（add-adapter / add-tool ...） | 参考 |
| `.agenthub-data/` | 运行时：`workspaces/` + `deployments/` + `skills/` + `worktrees/` | 前后端共用 |

---

## 13. 如何运行

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

1. **`ModelProfile.api_key`** — SDK (Custom) agent 的 API key 从 ModelProfile 解析（显式选中的 profile 或用户默认 profile）
2. **`user_settings.<provider>_api_key`** — 用户在「设置」面板全局自填，存 `user_settings` 表（按 `user_id` 分行）；用于 RAG / 记忆等非 agent 子系统
3. **`backend/.env`** — 环境变量兜底（dev / CI 友好；`config.py` 的 `apply_env_overrides()` 桥接到 `os.environ`）

> CLI 适配器（Claude Code / Codex）走 CLI 自带认证（OAuth / 环境变量），跳过 API key 解析与工具注入。详见 [CLAUDE.md](./CLAUDE.md) §5.4。

```env
# backend/.env
DATABASE_URL=postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
ARK_API_KEY=...
TAVILY_API_KEY=...           # web_search 工具
EMBEDDING_API_KEY=...        # RAG 语义检索
EMBEDDING_API_URL=...
EMBEDDING_MODEL=...
MILVUS_HOST=localhost        # 留空 = 禁用 Milvus
NEO4J_URI=bolt://localhost:7687     # 留空 = 禁用 Neo4j
ENABLE_GRAPH=false           # true 才启用知识图谱
MEMORY_ENABLED=true          # false = 禁用记忆 pipeline (节省 API 调用)
TRACE_ENABLED=true           # false = 禁用可观测性 (OTel 全 no-op)
PHOENIX_ENDPOINT=http://localhost:4317  # OTLP gRPC endpoint
PHOENIX_UI_URL=http://localhost:6006    # Phoenix Web UI
EVAL_RULE_ENABLED=true       # 在线规则评测 (默认开启)
EVAL_JUDGE_ENABLED=false     # 离线 LLM-as-Judge (默认关闭)
# ★ RAG 评测系统独立 LLM 配置
EVAL_LLM_API_KEY=...         # RAG 评测 Judge LLM
EVAL_LLM_API_URL=...
EVAL_LLM_MODEL=...
EVAL_DATASET_LLM_API_KEY=... # RAG benchmark 自动生成 LLM
EVAL_DATASET_LLM_API_URL=...
EVAL_DATASET_LLM_MODEL=...
# ★ RAG 任务队列
RAG_TASK_WORKER_ENABLED=true # false = 禁用异步任务队列 (降级为同步模式)
RAG_TASK_WORKER_INTERVAL=5   # 轮询间隔 (秒)
```

---

## 14. 基础设施降级矩阵

| 服务 | 配置为空时 | 影响 |
|---|---|---|
| PostgreSQL | — (必需) | 后端无法启动 |
| Milvus | `MILVUS_HOST` 空 | RAG 向量+全文检索退化；图谱向量召回不可用 |
| ~~Elasticsearch~~ | ~~`ES_ADDRESSES` 空~~ | ~~**已移除** — Milvus 原生 BM25 sparse vector 替代~~ |
| Neo4j | `NEO4J_URI` 空 或 `ENABLE_GRAPH=false` | KGStore no-op；RAG 无图谱检索 |
| Kafka | `KAFKA_BROKERS` 空 | 用 in-process EventBus（默认） |
| ~~Redis~~ | ~~`REDIS_URL` 空~~ | ~~**已移除** — 双 DB 架构下 SQLite 直写 + 进程内 dict TTL 缓存替代~~ |
| Phoenix | `TRACE_ENABLED=false` 或 Phoenix 不可达 | OTel `BatchSpanProcessor` 缓冲后静默丢弃，不阻断主链路 |
| Embedding API | `EMBEDDING_API_KEY` 空 | RAG 无语义检索能力 |
| LLM API (RAG 用) | 无任何 LLM key | RAG 无 rerank；KG 无实体抽取 |
| Memory Pipeline | `MEMORY_ENABLED=false` | 记忆 pipeline 全部关闭（节省 API 调用） |
| RAG Task Worker | `RAG_TASK_WORKER_ENABLED=false` | 降级为同步模式（上传时直接解析+索引） |
| RAG Eval LLM | `EVAL_LLM_API_KEY` 空 | RAG 评测系统不可用（不影响 RAG 检索） |

> 启动时后端打印状态面板，一目了然哪些服务已连接、哪些降级。

---

*本文档由整体目录与代码分析生成。深入某子系统请读 `specs/` 对应编号；协作规则见 [CLAUDE.md](./CLAUDE.md)；代码地图见 [OVERVIEW.md](./OVERVIEW.md)。最后更新：2026-08-19 · 同步 RAG 大重构（ES 移除 → Milvus BM25、OCR 引擎注册表、分块预设、文件生命周期、任务队列、图谱 v2 PPR + entity/triple vector、评测系统、DB 22→27 张表 + 路由 14+13、MEMORY_ENABLED 环境变量等）。*
