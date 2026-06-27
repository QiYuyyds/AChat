# AgentHub

<p align="center">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=0B1F2A">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white">
  <img alt="pnpm" src="https://img.shields.io/badge/pnpm-workspace-F69220?logo=pnpm&logoColor=white">
</p>

<p align="center">
  <b>简体中文</b>
</p>

AgentHub 是一个基于前端（Next.js + React）和 Python（FastAPI）实现的多 Agent 协作工作空间，把 AI 协作做成 IM 群聊式的体验。

它不把每次 agent 运行当成一段孤立的终端记录，而是围绕「会话」来组织工作：Agent 是联系人，会话是工作空间，文件与产物是共享上下文，Orchestrator 还能把一项工作拆给多个 Agent 并行完成。同时集成了 RAG 混合检索、分层记忆系统和 Document 知识库，让 Agent 拥有跨会话的知识与记忆能力。

<p align="center">
    <img src="docs/images/agenthub-preview.png" alt="AgentHub 多 Agent 协作与产物预览" width="100%" />
</p>

> 当前状态：本地开发中。Web 版可用；桌面版与移动伴随端开发中。

## 目录

- [为什么选 AgentHub](#为什么选-agenthub)
- [功能特性](#功能特性)
  - [IM 式 Agent 工作空间](#im-式-agent-工作空间)
  - [多 Agent 支持](#多-agent-支持)
  - [Orchestrator 与任务调度](#orchestrator-与任务调度)
  - [RAG 混合检索与知识库](#rag-混合检索与知识库)
  - [分层记忆系统](#分层记忆系统)
  - [产物与部署预览](#产物与部署预览)
- [技术栈](#技术栈)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [基础设施服务](#基础设施服务)
- [桌面应用](#桌面应用)
  - [指定 Electron 构建平台](#指定-electron-构建平台)
  - [SQLite ABI 说明](#sqlite-abi-说明)
- [移动伴随端](#移动伴随端)
- [常用命令](#常用命令)
- [架构](#架构)
- [安全模型](#安全模型)
- [已知限制](#已知限制)
- [参与贡献](#参与贡献)

---

## 为什么选 AgentHub

如今的编码 Agent 很强，但真实工作往往不止一个 prompt：

- 同时保持多个会话和工作空间
- 把工作分给不同的 Agent 和模型
- 查看推理过程、工具调用、文件写入、命令输出和产物
- 在改动落到工作空间前审批高风险操作
- 让 Agent 记住你的偏好，召回历史知识
- 把文档灌入知识库，让 Agent 按需检索
- 在桌面端继续工作，未来还能用手机监看

AgentHub 正是为这套工作流而生。它默认本地运行，使用 PostgreSQL，并把 Agent 的执行保留在你自己的机器上。

---

## 功能特性

### IM 式 Agent 工作空间

- 会话列表、群聊、@提及、未读状态、书签、置顶、引用回复、编辑重发、撤回、重新生成、归档。
- 消息是结构化的 parts，而不是一整块 markdown：文本、代码、思考、工具调用、工具结果、附件、产物引用、部署卡片、调度计划各自有不同的渲染。
- 工具调用在聊天流里可见，包括较长的 bash 命令及其输出。
- 全局搜索、斜杠命令菜单、消息高亮。

### 多 Agent 支持

| 适配器 | 适用场景 |
| --- | --- |
| Claude | 使用 Anthropic Messages API，带全套工具与会话续接。 |
| Custom Agent | 兼容 OpenAI Chat Completions 的 provider，如 OpenAI、DeepSeek、火山方舟、OpenRouter、SiliconFlow 等。 |
| Mock | 本地开发用，不消耗 token。 |

你可以在 UI 里创建自定义 Agent，自带模型、provider、system prompt、base URL、API key、工具集和 Skills。

### Orchestrator 与任务调度

Orchestrator 是一个带额外工具的普通 Agent。它可以：

- 提出结构化的澄清问题
- 制定任务计划（DAG 拓扑）
- 等待计划被批准或修订
- 把任务派发给子 Agent（并行调度 + 同波次冲突检测）
- 跟踪子任务的完成、失败、阻塞和产物
- 把最终结果聚合回会话

### RAG 混合检索与知识库

AgentHub 集成了完整的 RAG（检索增强生成）管线：

- **三路混合检索**：Milvus（向量语义）+ Elasticsearch（全文 BM25）+ Neo4j（知识图谱子图遍历），通过 RRF 融合排序。
- **Query Rewriting**：LLM 生成扩展查询，提升召回率。
- **Reranking**：LLM 对结果重排，提升精度。
- **Document + Version 知识库**：全局文档版本化管理，支持上传/Agent 生成，按需召回。文档解析支持 PDF（pdfplumber → PyPDF2 → pdftotext 三级降级）、Markdown 等。
- **会话级开关**：每个会话可独立开启/关闭 RAG 注入。

### 分层记忆系统

Agent 拥有跨会话的记忆能力：

- **短期记忆（STM）**：滑动窗口内的对话历史。
- **长期记忆（LTM）**：embedding 语义召回，带重要性评分。
- **用户偏好（Preference）**：从对话中提取的 KV 偏好。
- **图谱记忆（GraphMemory）**：Neo4j 存储记忆节点与关系。
- **自动固化与衰减**：记忆按触发阈值固化，按时间衰减，自动去重清理。
- **PromptAssembler**：将偏好、召回记忆、约束规则组装注入 Agent 的 system prompt。

### Workspace 文件与审批

- 每个会话有一个 workspace。
- Sandbox 模式把文件存在 `.agenthub-data/workspaces/<conversationId>` 下。
- Local 模式把会话绑定到一个真实的本地项目目录。
- `fs_read`、`fs_write`、`bash` 都被限制在生效的 workspace 目录内。
- Review 模式可以在文件写入前要求审批。
- 高风险 bash 命令可以在执行前要求审批。

### 产物与部署预览

Agent 可以创建并引用结构化产物：

- `web_app`：沙箱 iframe 预览
- `document`：markdown 渲染
- `image`：图片预览
- `ppt`：幻灯片预览 + 真 `.pptx` 导出
- `code_file`：workspace 文件引用
- `diff`：版本对比

对于本地前端项目，Agent 可以把 `dist`、`build`、`out`、`client/dist` 等静态输出目录发布到一张本地预览卡片里。

### 桌面与移动端

- 支持 Electron 桌面打包（可选）。
- `apps/mobile` 下有一个 Capacitor 移动伴随端。
- 设想的移动端工作方式是「伴随客户端」：手机通过 LAN 或 Tailscale 连到桌面端的 AgentHub host，然后观察运行、发消息、处理审批。

---

## 技术栈

### 前端
- Next.js 16 App Router + React 19
- TypeScript strict 模式
- Tailwind CSS v4 + shadcn/ui
- Zustand + Immer
- SSE 实时更新
- Electron 33 桌面打包（可选）
- Capacitor 移动伴随端
- pnpm workspaces

### 后端
- Python 3.11+
- FastAPI
- SQLAlchemy 2.0 async + asyncpg
- PostgreSQL 16
- Pydantic v2 数据验证
- AI SDKs: `anthropic` · `openai`（Python）

### 基础设施（Docker Compose，可降级）
- PostgreSQL 16 — 关系型主库
- Milvus v2.4.17 — 向量检索（RAG / LTM）
- Elasticsearch 8.14 — 全文检索（RAG BM25）
- Neo4j 5 — 知识图谱（KGStore / GraphMemory）
- Kafka（可选）— 事件总线增强

Next.js 锁定在 `16.2.6`。如果你要改动框架层的行为，先读 `node_modules/next/dist/docs/` 下的本地 Next 文档。

后端 Python 依赖通过 `backend/pyproject.toml` 或 `backend/requirements.txt` 管理。

---

## 环境要求

- Node.js 20+
- pnpm
- Python 3.11+
- PostgreSQL 16（或通过 Docker Compose 启动）
- Docker（用于启动基础设施服务，可选但推荐）
- 走桌面端路径需要 macOS 或 Windows
- 只有开发 iOS 伴随端时才需要 Xcode 和 CocoaPods

可选的 provider 配置：

- Anthropic、OpenAI、DeepSeek、火山方舟，或自定义 OpenAI 兼容 provider 的 API key
- Tavily API key（Web 搜索工具）
- Embedding API key（RAG / 记忆语义检索）

---

## 快速开始

### 1. 安装前端依赖

```powershell
pnpm install
```

### 2. 启动基础设施服务（推荐）

```powershell
docker compose -f docker-compose.infra.yml up -d
```

这会启动 PostgreSQL、Milvus、Elasticsearch、Neo4j。如果暂时不需要 RAG / 记忆 / 知识图谱，可以只启动 PostgreSQL：

```powershell
docker compose -f docker-compose.infra.yml up -d postgres
```

### 3. 安装后端依赖

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd ..
```

### 4. 配置环境变量

前端（项目根目录 `.env.local`）：

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

后端（`backend/.env`，从 `.env.example` 复制）：

```env
DATABASE_URL=postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub
ANTHROPIC_API_KEY=你的密钥
# 或 OPENAI_API_KEY / DEEPSEEK_API_KEY
```

完整配置（启用 RAG / 记忆 / 知识图谱）见 `backend/.env.example`。

### 5. 启动服务

```powershell
# 终端 A：启动后端
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# 终端 B：启动前端
$env:NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"; pnpm dev
```

打开：

```text
http://localhost:3000
```

后端 API 文档：

```text
http://localhost:8000/docs
```

首次启动时，后端会自动建表并 seed 内置 Agent。启动后查看后端终端的 **Startup Status** 面板，确认各服务连接状态。

API key 既可以配在 `backend/.env`，也可以在应用的设置面板里配。Agent 级别的 key 会覆盖全局设置。

> 更详细的启动指南见 [QUICKSTART.md](./QUICKSTART.md)。

---

## 基础设施服务

AgentHub 的基础设施服务通过 Docker Compose 管理，提供两种编排文件：

| 文件 | 用途 |
|---|---|
| `docker-compose.infra.yml` | 仅基础设施（PG/Milvus/ES/Neo4j），前后端在本机运行 |
| `docker-compose.yml` | 全栈容器化（前后端 + 基础设施） |

常用命令：

```powershell
# 启动全部基础设施
docker compose -f docker-compose.infra.yml up -d

# 查看状态
docker compose -f docker-compose.infra.yml ps

# 停止
docker compose -f docker-compose.infra.yml down
```

**降级策略**：每个基础设施服务独立 try/except，单个失败不影响其他。Milvus 挂 → 退化为 TF cosine；ES 挂 → 无全文检索；Neo4j 挂 → GraphMemory no-op。不配任何基础设施（仅 PostgreSQL）时，核心对话功能完全正常。

| 服务 | 端口 | 不配时的影响 |
|---|---|---|
| PostgreSQL | 5432 | **必需**，后端无法启动 |
| Milvus | 19530 | RAG 向量检索退化；LTM 退化为 TF cosine |
| Elasticsearch | 9200 | RAG 无全文检索 |
| Neo4j | 7474/7687 | GraphMemory no-op；RAG 无图谱检索 |

---

## 桌面应用

开发模式：

```powershell
pnpm electron:dev
```

默认打包命令：

```powershell
pnpm electron:build
```

产物输出到：

```text
release/
```

当前 `package.json#build` 配置的目标：

- macOS：`dmg`，`arm64`
- Windows：`nsis`，`x64`

### 指定 Electron 构建平台

`pnpm electron:build` 是个便捷脚本。如果你想精确选择平台/架构，就跑同一套 prebuild 流程，再带平台 flag 调 `electron-builder`：

```powershell
# macOS arm64 DMG
pnpm build; pnpm electron:prebuild; pnpm electron:tsc; pnpm exec electron-builder --mac dmg --arm64

# Windows x64 NSIS 安装包
pnpm build; pnpm electron:prebuild; pnpm electron:tsc; pnpm exec electron-builder --win nsis --x64
```

> ⚠️ 当前桌面版尚待改造：内嵌 Next 已无后端 API 路由，需改为启动独立的 Python 后端进程。

### SQLite ABI 说明

本项目前端保留了 `better-sqlite3`（用于前端行类型和 Electron 打包），会根据命令在 Node ABI 和 Electron ABI 之间切换：

- `pnpm dev`、`pnpm test`：Node ABI
- `pnpm build`、`pnpm start`、打包后的 Electron app：Electron ABI

如果你看到原生模块版本错误，跑下面之一：

```powershell
pnpm rebuild better-sqlite3
pnpm electron:rebuild
```

---

## 移动伴随端

移动端 workspace：

```text
apps/mobile
```

常用命令：

```powershell
pnpm mobile:dev
pnpm mobile:build
pnpm mobile:sync
pnpm mobile:open:ios
pnpm mobile:open:android
```

移动端被设计成通过 LAN 或 Tailscale 连接桌面端的 AgentHub host。Agent 执行、文件写入、命令执行和 workspace 状态都留在桌面侧。

---

## 常用命令

```powershell
pnpm dev                          # Web 开发服务
pnpm typecheck                    # TypeScript 检查
pnpm lint                         # ESLint
pnpm test                         # Vitest 单元测试

cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload  # Python 后端
cd backend; .\.venv\Scripts\python.exe -m pytest                          # 后端测试
cd backend; .\.venv\Scripts\python.exe -m ruff check .                    # 后端 lint

docker compose -f docker-compose.infra.yml up -d   # 启动基础设施
pnpm electron:dev                  # 桌面开发模式
pnpm electron:build                # 桌面打包
```

本地数据：

```text
.agenthub-data/workspaces/     # workspace 文件
.agenthub-data/deployments/    # 部署产物
.agenthub-data/skills/         # Agent Skills
```

---

## 架构

AgentHub 采用前后端分离架构：

```
┌──────────────────────────────────────────┐
│         前端 (Next.js + React)            │
│  L5 UI: React 组件、shadcn/ui             │
│  L4 State: Zustand store、SSE 客户端       │
└──────────────┬───────────────────────────┘
               │ HTTP / SSE
┌──────────────▼───────────────────────────┐
│         后端 (Python + FastAPI)           │
│  L3 Application Services                  │
│    AgentRunner、Orchestrator、             │
│    ConversationService、EventBus、         │
│    ToolExecutor、RAGService、              │
│    DocumentService、PromptAssembler       │
│  L2 Agent Platform Adapters               │
│    Claude、Custom、Mock                    │
│  L1 Persistence                           │
│    SQLAlchemy、PostgreSQL、workspace FS    │
├──────────────────────────────────────────┤
│  Infrastructure (可选, 独立降级)            │
│    Milvus(向量) · ES(全文) · Neo4j(图谱)   │
│    RAG混合检索 · 分层记忆 · 知识图谱        │
└──────────────────────────────────────────┘
```

核心契约是 `StreamEvent`。适配器输出、工具活动、产物创建、待审批、调度状态、用量更新，都先汇入这个事件模型，再通过 SSE 到达前端 UI。

关键文档：

- [CLAUDE.md](./CLAUDE.md)：给 AI 协作者的项目规则
- [OVERVIEW.md](./OVERVIEW.md)：代码地图与当前实现状态
- [ARCHITECTURE.md](./ARCHITECTURE.md)：架构与目录详解
- [QUICKSTART.md](./QUICKSTART.md)：快速启动指南
- [openspec/project.md](./openspec/project.md)：OpenSpec 能力索引
- [specs/](./specs)：编号版详细规格
- [backend/](./backend)：Python 后端服务代码

---

## 安全模型

AgentHub 假定 LLM 的输出是不可信输入。

- 文件工具把路径解析到会话生效的 workspace 之内。
- Bash 命令在 workspace cwd 内运行。
- 危险的 bash 模式会被拦截（POSIX / Windows 双平台黑名单）。
- 高风险命令可以要求审批。
- 生成的 web app 产物在沙箱 iframe 里渲染（`sandbox="allow-scripts"`，不给 `allow-same-origin`）。
- API key 是本地设置或环境变量；没有任何托管的 key 服务。

这是一个本地单用户应用，不是多租户托管服务。

---

## 已知限制

- 桌面版尚待改造：内嵌 Next 已无后端 API 路由，需改为启动独立 Python 后端。
- 带原生模块的跨平台 Electron 构建，应该通过目标平台机器或 CI 处理。
- Claude / Custom Adapter 可以通过它自己的工具层写文件；sandbox 配额只对 AgentHub 托管的文件工具生效。
- 移动端是伴随客户端，不是独立的 Agent 运行时。
- 基础设施服务（Milvus / ES / Neo4j）不配时功能降级，但不影响核心对话。

---

## 参与贡献

改代码前，先读：

1. [CLAUDE.md](./CLAUDE.md) — 项目协作规则
2. [OVERVIEW.md](./OVERVIEW.md) — 代码地图与实现状态
3. [openspec/project.md](./openspec/project.md) — OpenSpec 能力索引
4. [openspec/specs](./openspec/specs) 和 [specs](./specs) 下的相关文件

当你改动实体、流式事件、工具、适配器、持久化、平台行为或安全规则时，代码和 spec 要一起更新。

---

## License

AGPL-3.0-only
