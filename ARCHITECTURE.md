# AgentHub 架构与目录说明

> 本文档描述 **后端已迁移到 Python (FastAPI)** 之后的项目整体架构、目录结构与数据流。
> 生成于 2026-06-25,迁移完成后。规则约定见 `CLAUDE.md`,详细契约见 `specs/`。

---

## 1. 项目定位

**AgentHub** 是一个 local-first 的多 Agent 协作平台。一句话:

> 把多 Agent 协作做成 IM 群聊体验。Agent 是「联系人」,对话是「工作空间」,Orchestrator 是「群里的项目经理」。

**核心能力**:IM 范式会话管理 · 统一适配器层接入 Claude/Codex/自建 Agent · Orchestrator 自动拆任务并行调度 · 产物(代码/网页/文档)内联预览与二次编辑 · 每会话独立 workspace 沙箱。

**运行形态**:本地运行,SQLite 文件数据库,前后端分离。

---

## 2. 技术栈(迁移后)

| 层 | 选型 |
|---|---|
| 前端框架 | Next.js 16 (App Router) + React 19 |
| 前端语言/样式/状态 | TypeScript (strict) · Tailwind + shadcn/ui · Zustand + Immer |
| **后端框架** | **FastAPI (Python 3.11)** |
| **后端 ORM / 验证** | **SQLAlchemy 2.0 async + aiosqlite · Pydantic v2** |
| 数据库 | SQLite |
| 流式传输 | SSE(一条全局连接) |
| AI SDK(后端) | `anthropic` · `openai`(Python) |
| 包管理 | pnpm(前端) · pip/venv(后端) |

> **迁移要点**:数据库结构、API 契约、SSE 事件格式保持**字节级兼容**;前端几乎不动(仅切换可配 API base URL)。

---

## 3. 五层架构

```
┌─────────────────────────────────────────────────────────────┐
│ L5  UI 组件 (React / shadcn)              src/components/      │  ← 前端
│ L4  State + Transport (Zustand + SSE)     src/stores/ src/lib/ │  ← 前端
├─────────────────────────────────────────────────────────────┤
│                    HTTP (REST + SSE)  ↕  跨进程边界            │
├─────────────────────────────────────────────────────────────┤
│ L3  Application Services                  backend/app/services/│  ← Python
│     (AgentRunner · Orchestrator · ConversationService ·       │
│      EventBus · ToolExecutor · ...)                           │
│ L2  Agent Platform Adapters               backend/app/adapters/│  ← Python
│     (Claude · Custom · Mock)                                  │
│ L1  Persistence (SQLAlchemy + SQLite + workspace 文件系统)    backend/app/db/ │  ← Python
└─────────────────────────────────────────────────────────────┘
```

**铁律**:UI 永不直接调 LLM,必经 L3 · Adapter 永不写 DB(只翻译事件流) · 工具执行属 L3 · Orchestrator 是特殊 Agent 走同一 AgentRunner。

---

## 4. 顶层目录地图

```
bitdance-agenthub-main/
├── backend/              ★ Python 后端(L1-L3 + 适配器)—— 全部业务逻辑
├── src/                  前端(L4-L5)+ 共享类型
│   ├── app/              Next.js 页面(layout/page,API 路由已删)
│   ├── components/       59 个 React 组件(聊天/产物/Agent 库...)
│   ├── lib/              前端工具 + api.ts(REST 客户端)+ config.ts(API base)
│   ├── stores/           Zustand store(app-store / search-store)
│   ├── shared/           ★ 共享类型(StreamEvent/MessagePart...)前后端契约源
│   └── db/schema.ts      ★ 仅保留(前端 import 行类型;DB 实体由后端 SQLAlchemy 拥有)
├── electron/             桌面版外壳(待改造:内嵌 Next 已无后端)
├── apps/mobile/          移动伴随 App(Capacitor)
├── packages/             共享包(workspace)
├── specs/                ★ 18 份编号详细规格(语言无关契约)
├── openspec/             OpenSpec 能力契约 + 变更提案
├── skills/               可复用开发任务模板
├── scripts/              构建/Electron/SQLite 辅助脚本(.mjs)
├── public/ docs/         静态资源 / 文档
├── .agenthub-data/       运行时数据(SQLite DB + workspaces + deployments)
├── CLAUDE.md             ★ AI 协作规则(怎么做/不做什么)
├── OVERVIEW.md           代码地图(做了什么/在哪)
└── ARCHITECTURE.md       本文档
```

`★` = 理解项目最关键的入口。

---

## 5. 后端深度剖析 (`backend/`)

```
backend/
├── app/
│   ├── main.py              FastAPI 入口:路由接线 + CORS + 启动加载 key
│   ├── config.py           配置(pydantic-settings)+ .env key 桥接到 os.environ
│   │
│   ├── db/ (3)             【L1 持久化】
│   │   ├── models.py        9 张表 SQLAlchemy 模型
│   │   └── engine.py        异步引擎 + SQLite(外键 ON / WAL / busy_timeout)
│   │
│   ├── schemas/ (6)        【类型契约 Pydantic】
│   │   ├── events.py        30+ StreamEvent(SSE 协议,snake_case 字段 + camelCase 别名)
│   │   ├── messages.py      MessagePart(parts 数组)
│   │   ├── artifacts.py     Artifact 内容类型
│   │   ├── dispatch.py      调度计划/任务
│   │   └── requests.py      API 请求/响应模型
│   │
│   ├── services/ (26)      【L3 业务逻辑 —— 核心大头】
│   │   ├── agent_runner.py        ★ 执行器(simple 路径 + 共享机制,1365 行)
│   │   ├── orchestrator.py        ★ 编排器三阶段调度(1369 行)
│   │   ├── orchestrator_prompts.py编排 prompt/XML 构建
│   │   ├── conversation_service.py会话/消息全生命周期
│   │   ├── event_bus.py           SSE 事件总线(asyncio.Queue 扇出)
│   │   ├── dispatch_plan.py       计划校验 / DAG 拓扑
│   │   ├── conversation_context.py跨 run 历史注入
│   │   ├── artifact_service.py    产物 CRUD / 版本链
│   │   ├── settings_service.py    全局设置 / API key 解析
│   │   ├── fs_service.py          workspace 文件读写 + 沙箱配额
│   │   ├── deployment_service.py  产物部署 + 资源/zip
│   │   ├── search_service.py      消息全文搜索
│   │   ├── task_result_report.py  子任务上报 + 完成度门禁
│   │   └── pending_*.py           审批/提问/命令/计划 内存 store
│   │
│   ├── adapters/ (8)       【L2 适配器】stream(input, cancel_event) -> AsyncIterator[StreamEvent]
│   │   ├── base.py          AdapterInput + ABC
│   │   ├── mock_adapter.py  Mock(脚本流,不烧 token)
│   │   ├── custom_adapter.py OpenAI 兼容(DeepSeek/火山方舟等,工具循环 MAX_TURNS=8)
│   │   ├── claude_adapter.py Anthropic Messages API
│   │   └── custom_provider_client.py / registry.py / session_store.py
│   │
│   ├── tools/ (15)         【工具系统】12 个内置工具
│   │   ├── base.py / registry.py  ToolContext(asyncio.Event 取消) + 注册表
│   │   ├── write_artifact / read_artifact
│   │   ├── fs_read / fs_write / fs_list / bash(黑名单 + 审批)
│   │   ├── ask_user / plan_tasks / report_task_result
│   │   └── deploy_artifact / deploy_workspace / read_attachment
│   │
│   ├── api/ (13)           【API 路由】51 路由 → 10 文件
│   │   └── conversations / messages / agents / artifacts / attachments /
│   │       fs / pending / settings / runs_misc / mobile / stream(SSE)
│   │
│   └── utils/ (13)         跨平台 · 安全黑名单 · ID · token 估算 · 审批 helper ...
│
└── tests/ (31)            390 个测试,全部通过;ruff 全绿
```

### 关键技术映射(TS → Python)

| TypeScript | Python |
|---|---|
| Drizzle ORM | SQLAlchemy 2.0 |
| Zod | Pydantic v2 |
| AsyncIterable | async generators |
| AbortSignal | asyncio.Event |
| EventEmitter | asyncio.Queue + 订阅者 |
| Promise / Future | asyncio.Task / Future |
| `Date.now()` | `now_ms()` |

> **数据契约**:DB 内 JSON(parts/agent_ids/usage)与 SSE 事件**全程 camelCase**;Pydantic 用 snake_case 字段 + camelCase 别名(`populate_by_name=True`),与前端及原 TS 行字节兼容。

---

## 6. 数据库:9 张表

| 表 | 说明 |
|---|---|
| `agents` | AI 代理(name / adapter_name / system_prompt / tool_names / api_key) |
| `conversations` | 会话(mode single/group / agent_ids / pinned_message_ids) |
| `messages` | 消息(role / parts JSON / status / run_id) |
| `artifacts` | 产物(type / content JSON / version / parent_artifact_id) |
| `workspaces` | 工作区(mode sandbox/local / root_path / bound_path) |
| `attachments` | 附件(kind image/file / file_path / mime_type) |
| `agent_runs` | 运行记录(status / usage JSON / parent_run_id) |
| `conversation_context_summaries` | 上下文压缩摘要 |
| `app_settings` | 全局设置单行表(各 provider 的 API key) |

---

## 7. 一条消息的生命周期(数据流)

```
用户在 UI 输入并发送
  └─ src/lib/api.ts  POST /api/conversations/{id}/messages
       └─ L3 conversation_service.send_message()
            ├─ 持久化用户 message
            ├─ 决策响应者(单聊/群聊)
            └─ runner_registry → AgentRunner.run()  (起 asyncio.Task,立即返回)
                 └─ agent_runner.execute_run()
                      ├─ build_adapter_input()  历史注入 + token 预算 + key 选择
                      ├─ adapter.stream()  ← L2(Claude/Custom/Mock)
                      │     产出 StreamEvent:part.delta / tool.call / artifact.create ...
                      │     工具调用 → tool_registry.execute()(沙箱内)
                      └─ consume_stream()
                           ├─ persist_event()  事件落 DB
                           └─ event_bus.publish()  → SSE
                                └─ GET /api/stream(一条全局连接)
                                     └─ 前端 stream-provider.tsx onmessage
                                          └─ Zustand store.applyEvent()  → UI 实时更新
```

**编排场景**:若目标 agent 是 Orchestrator,`execute_run` 转 `orchestrator.execute_orchestrator_run()` → PLAN(plan_tasks)→ 人工审批 → EXECUTE(DAG 调度 + 并发 + 子任务重试 + 冲突检测)→ AGGREGATE。

---

## 8. 前端结构 (`src/`)

| 目录 | 内容 |
|---|---|
| `app/` | `layout.tsx` / `page.tsx`(挂载 StreamProvider + 主界面);全局样式 |
| `components/` (59) | ChatPanel / MessageList / ArtifactPreviewPanel / AgentLibrary / CreateAgentDialog ... |
| `lib/` | `api.ts`(REST 客户端,统一 `API_BASE_URL` 前缀)· `config.ts`(读 `NEXT_PUBLIC_API_BASE_URL`)· 工具 |
| `stores/` | `app-store.ts`(会话/消息/事件 reducer)· `search-store.ts` |
| `shared/` (14) | StreamEvent / MessagePart / Artifact 等**前后端共享类型**(纯类型,无逻辑) |
| `db/schema.ts` | 仅保留:前端 import 行类型(AgentRow 等);它依赖 drizzle-orm + shared |

**前后端边界**:前端只通过 `lib/api.ts`(REST)和 `stream-provider.tsx`(SSE EventSource)与 Python 后端通信,两者都加 `API_BASE_URL` 前缀;默认空串=同源,设环境变量即指向独立 Python 后端。

---

## 9. 其它目录

| 目录 | 说明 | 当前状态 |
|---|---|---|
| `specs/` | 18 份编号详细规格(实体/事件/适配器/工具/编排/DB...),**语言无关契约**,迁移时作标准答案对照 | 有效 |
| `openspec/` | OpenSpec 能力契约 + 变更提案(changes/) | 有效 |
| `electron/` | 桌面版(main.ts 启动内嵌 Next server) | ⚠️ 待改造:内嵌 Next 已无后端,需改启 Python |
| `apps/mobile/` | 移动伴随 App(Capacitor / 远程审批,spec 14) | 独立模块 |
| `scripts/` | 构建/Electron/SQLite ABI 辅助(.mjs) | 部分(better-sqlite3 相关)前端已不需要 |
| `skills/` | 可复用开发任务模板(add-adapter / add-tool ...) | 参考 |
| `.agenthub-data/` | 运行时:`agenthub.db` + `workspaces/` + `deployments/` | 前后端共用 |

---

## 10. 如何运行(前后端分离)

**后端(终端 A)**
```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --reload
```

**前端(终端 B)**
```powershell
$env:NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"; pnpm dev
```
浏览器打开 http://localhost:3000。

**API Key**(优先级:agent 自带 > 设置面板(app_settings 表)> `backend/.env` / 环境变量)
```
# backend/.env
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
ARK_API_KEY=...
```

---

## 11. 迁移状态

**✅ 已完成(web 迁移 100%)**:9 表 + 30+ 事件 + 12 工具 + 3 适配器(Mock/Custom/Claude)+ AgentRunner(simple + orchestrator)+ 51 路由 + SSE 全部移植;390 测试通过,ruff 全绿;前端 typecheck 绿;端到端实测通过。

**⏳ 延后(非阻塞)**:
- `/api/conversations/{id}/compact` 真实上下文压缩(需移植 LLM 摘要流)
- Electron 桌面版改为启动 Python 后端
- Codex 适配器 · Claude 扩展 thinking · 外部 MCP 接入(spec 15)
- 孤儿配置清理:`drizzle.config.ts` / `playwright.config.ts` 及对应 devDeps

---

*本文档由整体目录与代码分析生成。深入某子系统请读 `specs/` 对应编号;协作规则见 `CLAUDE.md`。*
