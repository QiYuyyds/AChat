# AChat

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

AChat 是一个基于前端（Next.js + React）和 Python（FastAPI）实现的多 Agent 协作工作空间，把 AI 协作做成 IM 群聊式的体验。

它不把每次 agent 运行当成一段孤立的终端记录，而是围绕「会话」来组织工作：Agent 是联系人，会话是工作空间，文件与产物是共享上下文，Orchestrator 还能把一项工作拆给多个 Agent 并行完成。同时集成了用户认证与多用户隔离、RAG 混合检索、分层记忆系统和 Document 知识库，让 Agent 拥有跨会话的知识与记忆能力。

<p align="center">
    <img src="docs/AChat封面.gif" alt="AChat 封面" width="100%" />
</p>

> 当前状态：本地开发中。Web 版可用；桌面版与移动伴随端开发中。

## 目录

- [为什么选 AChat](#为什么选-agenthub)
  - [功能演示](#功能演示)
- [功能特性](#功能特性)
  - [IM 式 Agent 工作空间](#im-式-agent-工作空间)
  - [用户认证与多用户隔离](#用户认证与多用户隔离)
  - [多 Agent 支持](#多-agent-支持)
  - [小A 全局悬浮助手](#小a-全局悬浮助手)
  - [Orchestrator 与任务调度](#orchestrator-与任务调度)
  - [RAG 混合检索与知识库](#rag-混合检索与知识库)
  - [分层记忆系统](#分层记忆系统)
  - [Workspace 文件与审批](#workspace-文件与审批)
  - [产物与部署预览](#产物与部署预览)
  - [代码图谱智能与执行计划](#代码图谱智能与执行计划)
  - [Obsidian 知识同步与外部 MCP](#obsidian-知识同步与外部-mcp)
  - [Run 内压缩](#run-内压缩)
  - [生命周期 Hooks 与 Checkpoint](#生命周期-hooks-与-checkpoint)
  - [Agent 可观测性与评测](#agent-可观测性与评测)
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

## 为什么选 AChat

如今的编码 Agent 很强，但真实工作往往不止一个 prompt：

- 同时保持多个会话和工作空间
- 把工作分给不同的 Agent 和模型
- 查看推理过程、工具调用、文件写入、命令输出和产物
- 在改动落到工作空间前审批高风险操作
- 让 Agent 记住你的偏好，召回历史知识
- 把文档灌入知识库，让 Agent 按需检索
- 在桌面端继续工作，未来还能用手机监看

AChat 正是为这套工作流而生。它默认本地运行，使用 PostgreSQL，并把 Agent 的执行保留在你自己的机器上。

### 功能演示

<p align="center">
    <img src="docs/功能演示.gif" alt="AChat 功能演示" width="100%" />
</p>

---

## 功能特性

### IM 式 Agent 工作空间

- 会话列表、群聊、@提及、未读状态、书签、置顶、引用回复、编辑重发、撤回、重新生成、归档。
- 消息是结构化的 parts，而不是一整块 markdown：文本、代码、思考、工具调用、工具结果、附件、产物引用、部署卡片、调度计划各自有不同的渲染。
- 工具调用在聊天流里可见，包括较长的 bash 命令及其输出。
- 全局搜索、斜杠命令菜单、消息高亮。

### 用户认证与多用户隔离

- **注册 / 登录**：密码用 bcrypt（cost factor 12）哈希存储，JWT 分 access token（1h）和 refresh token（7d），存在 HttpOnly cookie 中。
- **多用户隔离**：所有用户数据表通过 `user_id` 列隔离（Agent / Conversation / Document / McpServer / LongTermMemory / UserSettings / UserPreference 等）。builtin agent 的 `user_id IS NULL`，所有用户共享。
- **CSRF 防护**：POST / PATCH / DELETE 请求必须携带匹配的 `Origin` header。
- **SSE 认证**：同源时自动携带 cookie；跨域 dev 时 SSE 连接通过 `?token=` query param 认证。
- **个人资料管理**：用户可以设置显示名称和头像。
- **CLI Agent 隔离**：Claude Code / Codex 子进程的 `HOME` / `USERPROFILE` 按用户隔离，确保 CLI 认证状态独立。
- **token_version 全局吊销**：改密码或 logout-all 时 +1，所有旧 token 立即失效。

### 多 Agent 支持

| 适配器 | 路线 | 适用场景 |
| --- | --- | --- |
| Claude Code | CLI 子进程 | 拉起本机 `claude` CLI（stream-json 协议），CLI 自带工具、沙箱与审批；AChat 通过 MCP bridge 补充平台工具。 |
| Codex | CLI 子进程 | 拉起本机 `codex app-server`（JSON-RPC 2.0），代码就绪，端到端联调中。 |
| Custom Agent | SDK | 兼容 OpenAI Chat Completions 的 provider，如 OpenAI、DeepSeek、火山方舟、OpenRouter、SiliconFlow 等。 |
| 小A Guide Agent | SDK (builtin) | ★ 全局悬浮助手，builtin + `is_guide=True`，走 custom adapter SDK 路线，仅注入 7 个管理工具 + `ask_user`，开箱即用（默认 DeepSeek 兜底）。 |
| Mock | 脚本 | 本地开发用，不消耗 token。 |

> Claude Code 与 Codex 走 **CLI 子进程路线**：工具执行、沙箱、审批由 CLI 自管，AChat 只翻译事件流。后续还规划接入 Hermes、OpenClaw、OpenCode 等 CLI agent。迁移方案见 `openspec/changes/migrate-claude-codex-to-cli/`。

你可以在 UI 里创建自定义 Agent，自带模型、provider、system prompt、base URL、API key、工具集和 Skills。Custom Agent 提供 4 种角色预设（程序员 / 调研员 / 协调者 / 写作），每种预设自带匹配的 system prompt 和工具推荐。所有 custom agent 自带 9 个基础工具（文件读写、bash、ask_user 等），另可从 5 个可选工具中勾选（产物创建、部署、web 搜索等）。

### 小A 全局悬浮助手

AChat 内置一个名为「小A」的 Guide Agent，作为系统的「门面引导」，以全局悬浮助手形态常驻：

- **开箱即用**：小A 是 builtin agent（`ag_guide_builtin`），后端启动时幂等种子创建。默认走 DeepSeek provider，配 `DEEPSEEK_API_KEY` 即可用；也支持通过 `GUIDE_AGENT_*` 环境变量切换 provider/model/key。
- **自然语言驱动管理**：用户用自然语言就能完成建/改 Agent、管 Skill/MCP/知识库、整理记忆/偏好、改画像、查看会话与活动等操作，无需手动点 UI。
- **7 个管理工具**：`manage_agents` / `manage_skills` / `manage_mcp` / `manage_documents` / `manage_memory` / `manage_profile` / `manage_conversations`，仅对 guide agent 注入；非 guide agent 即使误配也会被过滤。
- **智能记忆整理**：`manage_memory(action=optimize)` 走 LLM 驱动的智能整理（删除垃圾 + 合并重复 + 提炼升华 + 重新生成 embedding），与现有算法驱动 `consolidate()` 互补。
- **双活跃会话模型**：工作会话（主聊天面板）和 guide 会话（悬浮面板）并行运行，互不干扰。guide 会话 (`mode='guide'`) 不出现在会话列表、不可删除、不出现在全局搜索。
- **悬浮面板 UX**：`GuideFloatingPanel` 组件支持拖拽、缩放、收起/展开、`Ctrl/Cmd+G` 快捷键唤起，位置和尺寸存 localStorage；移动端全屏覆盖。精简 MessageList 只渲染 text / tool_use / ask_user 三种 part。
- **副作用事件刷新**：管理工具执行成功后发送 `guide_side_effect` SSE 事件，前端按 target 刷新对应面板（Agents / Skills / MCP / 知识库 / 记忆 / Profile / 会话列表）。
- **边界铁律**：小A 只做管理，不写代码、不编辑文件、不跑命令、不产产物、不派发子任务。不能修改/删除 builtin Agent，不能改自己。创建 Agent 只支持 Custom Agent（SDK 路线）。

### Orchestrator 与任务调度

AChat 使用统一 Agent Loop：所有 Agent（solo / coordinated / subagent）走同一个 `run_agent_loop` while-loop，区别仅在于工具列表和 system prompt（详见 `specs/19`）。

- **Solo 模式**：单聊会话默认。Agent 拥有自己的工具集，还可以通过 `task_dispatch` 克隆自己来处理子任务（递归深度上限 `MAX_DISPATCH_DEPTH = 3`）。
- **Coordinated 模式**：群聊中 Orchestrator 的模式。除了 `task_dispatch`，还拥有 `dispatch_plan` 工具来声明结构化 DAG（拓扑排序 + 波调度并行执行 + 级联跳过）。
- **Subagent 模式**：`task_dispatch` / `dispatch_plan` 触发的子 Agent 运行。使用隔离的任务提示，不注入对话历史；clone-self 派发的消息 `hidden=true`，不显示在聊天视图。

Orchestrator 可以：

- 提出结构化的澄清问题（`ask_user`）
- 用 `task_dispatch` 即时派发单个子任务
- 用 `dispatch_plan` 声明 DAG 计划（多任务 + 依赖关系 + 并行执行）
- 等待计划被批准或修订（可选审批流程）
- 跟踪子任务的完成和失败
- 把最终结果聚合回会话（自然 `end_turn`，无单独聚合阶段）

没有旧三阶段流程的验证门禁、重试 harness 或 LLM judge——Agent 根据子任务返回结果自行决策。

### RAG 混合检索与知识库

AChat 集成了完整的 RAG（检索增强生成）管线：

- **三路混合检索**：Milvus（向量语义）+ Elasticsearch（全文 BM25）+ Neo4j（知识图谱子图遍历），通过 RRF 融合排序。
- **Query Rewriting**：LLM 生成扩展查询，提升召回率。
- **Reranking**：LLM 对结果重排，提升精度。
- **Document + Version 知识库**：全局文档版本化管理，支持上传/Agent 生成，按需召回。文档解析支持 PDF（pdfplumber → PyPDF2 → pdftotext 三级降级）、Markdown 等。
- **会话级开关**：每个会话可独立开启/关闭 RAG 注入。

### 分层记忆系统

Agent 拥有跨会话的记忆能力：

- **短期记忆（STM）**：滑动窗口内的对话历史。
- **会话记忆（SessionMemory）**：跨 run 的会话级上下文。
- **长期记忆（LTM）**：embedding 语义召回，带重要性评分。
- **用户偏好（Preference）**：从对话中提取的 KV 偏好。
- **图谱记忆（GraphMemory）**：Neo4j 存储记忆节点与关系。
- **自动固化与衰减**：记忆按触发阈值固化，按时间衰减，自动去重清理。
- **PromptAssembler**：将偏好、召回记忆、约束规则组装注入 Agent 的 system prompt。

### Workspace 文件与审批

- 每个会话有一个 workspace。
- Sandbox 模式把文件存在 `.agenthub-data/workspaces/<conversationId>` 下。
- Local 模式把会话绑定到一个真实的本地项目目录。
- 文件工具 `fs_read`、`fs_write`、`fs_edit`、`fs_list`、`fs_glob`、`fs_grep` 和 `bash` 都被限制在生效的 workspace 目录内。
- Review 模式可以在文件写入前要求审批。
- 高风险 bash 命令可以在执行前要求审批。
- **Worktree 隔离**：DAG 波调度并行任务可用 git worktree 隔离，非 git 目录用目录拷贝降级，自动 merge-back。
- **环境变量隔离**：按会话/用户隔离环境变量，CLI Agent 的 `HOME`/`USERPROFILE` 按用户隔离。

### 产物与部署预览

Agent 可以创建并引用结构化产物：

- `web_app`：沙箱 iframe 预览
- `document`：markdown 渲染
- `image`：图片预览
- `ppt`：幻灯片预览 + 真 `.pptx` 导出
- `code_file`：workspace 文件引用
- `diff`：版本对比

对于本地前端项目，Agent 可以把 `dist`、`build`、`out`、`client/dist` 等静态输出目录发布到一张本地预览卡片里。

### 代码图谱智能与执行计划

- **代码图谱智能**：集成 CodeGraph 本地运行时，Agent 可以通过 `code_explore` 工具探索项目代码结构（符号索引、引用查找、类型层级）。支持后台异步索引、防抖同步、状态机管理。
- **执行计划工具**：Agent 可以用 `create_plan` 创建结构化执行计划，用 `plan_step` / `add_plan_steps` 更新步骤状态。计划以卡片形式渲染在聊天 UI 中，用户可实时看到工作进度。

### Obsidian 知识同步与外部 MCP

- **Obsidian vault 同步**：把 Obsidian vault 同步到 AChat 知识库，自动解析 wikilink 和 frontmatter，预处理后入 RAG。
- **外部 MCP 接入**：支持配置外部 MCP Server（stdio / SSE 传输），Agent 可调用外部 MCP 工具。MCP 调用可配置审批。AChat 自身的平台工具也通过 MCP bridge 暴露给 CLI agent。

### Run 内压缩

SDK Agent 在 ReAct loop 中内置五阶段递进压缩 pipeline，在 context window 占用达到阈值时自动触发：

- Stage 1（ratio ≥ 0.70）：语义摘要旧 tool 结果
- Stage 2（ratio ≥ 0.80）：更激进地重裁 Stage 1 摘要
- Stage 3（ratio ≥ 0.88）：将更旧轮次折叠为单个 marker
- Stage 4（ratio ≥ 0.93）：软收尾注入
- Stage 5（ratio ≥ 0.95）：强制终止

Stage 1/2/3 为纯结构化裁剪（无 LLM 调用），独立于跨 run 的上下文压缩。

### 生命周期 Hooks 与 Checkpoint

Agent 运行支持可插拔的生命周期 Hooks 系统：

- **7 个内置 Hook**：审计日志（audit_log）、自动压缩（auto_compact）、检查点保存（checkpoint）、记忆持久化（memory_persist）、技能自动激活（skill_auto_activator）、摘要生成（summary_generate）、工具审批（tool_approval）。
- **10 个生命周期事件**：`pre_turn` / `post_turn` / `pre_tool_use` / `post_tool_use` / `on_stop` / `on_error` / `on_run_start` / `on_run_end` / `on_message_end` / `on_task_verified`。
- Agent 通过 `hook_names` 字段按需启用 Hook 组。
- **Checkpoint**：SDK Agent 支持 turn 级检查点保存与恢复（`agent_run_checkpoints` 表）。

### Agent 可观测性与评测

AChat 集成了基于 OpenTelemetry 的全链路追踪和评测系统：

- **OpenTelemetry SDK 采集**：FastAPI / httpx / openai 自动 instrumentation（零侵入覆盖 HTTP 请求与 LLM 外调）+ Level 4 深度手动埋点（18 处：agent run / 上下文组装 / 提示词组装 / 记忆召回 / RAG 三路检索子步骤 / 每轮 LLM 生成 / 工具调用 / 子 Agent 派发）。
- **Arize Phoenix**：独立 Docker 部署的可观测性后端（:6006 Web UI + :4317 OTLP gRPC），提供 Trace 瀑布流可视化 + Eval 评分展示。存储复用 PostgreSQL 独立 database `achat_observability`。
- **Span 中英文映射**：span name 采用「英文标识 · 中文描述」格式（如 `agent.run · 代理运行`），Phoenix UI 直接显示中文，测试人员可读。
- **在线规则评测**（默认开启）：每次 agent run 结束后自动从 trace 数据计算 14 项指标（任务完成率 / 工具成功率 / 轮次效率 / token 消耗 / 派发深度 / 并行度等），eval score 挂在 trace 上，不调 LLM，耗时 < 10ms。
- **离线 LLM-as-Judge 评测**（默认关闭，手动触发）：`POST /api/eval/judge/{trace_id}` 从 Phoenix 拉取指定 trace，调用 LLM 深度评判 9 个维度（工具选择准确性 / 子任务粒度 / 聚合忠实度 / 回答忠实度等）。
- **评测指标体系**：Agent 全过程评测（5 维度：任务完成 / 工具调用质量 / 步骤效率 / 提示词效果 / 回答质量）+ 多 Agent 协作评测（4 维度：任务拆解 / 调度效率 / 子 Agent 质量 / 聚合质量）。
- **`trace_enabled` 开关**：关闭时所有埋点变为 no-op，不影响主链路。Phoenix 不可达时 OTel SDK 缓冲后静默丢弃，不报错。

### 桌面与移动端

- 支持 Electron 桌面打包（可选）。
- `apps/mobile` 下有一个 Capacitor 移动伴随端。
- 设想的移动端工作方式是「伴随客户端」：手机通过 LAN 或 Tailscale 连到桌面端的 AChat host，然后观察运行、发消息、处理审批。

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
- 认证: bcrypt + PyJWT（JWT HttpOnly cookie）
- AI 适配器: Claude Code / Codex 走 **CLI 子进程路线**（stream-json / JSON-RPC 2.0）；Custom 走 `openai` Python SDK（Chat Completions + 自驱 tool loop）；AChat MCP bridge 暴露平台工具给 CLI agent

### 基础设施（Docker Compose，可降级）
- PostgreSQL 16 — 关系型主库
- Milvus v2.4.17 — 向量检索（RAG / LTM）
- Elasticsearch 8.14 — 全文检索（RAG BM25）
- Neo4j 5 — 知识图谱（KGStore / GraphMemory）
- Kafka（可选）— 事件总线增强
- Redis 7 — 元数据缓存 + 异步 DB 写入（KV cache / Stream write-behind）
- Phoenix — Agent 可观测性后端（OpenTelemetry Trace + Eval 评分，:6006 Web UI）

Next.js 锁定在 `16.2.6`。如果你要改动框架层的行为，先读 `node_modules/next/dist/docs/` 下的本地 Next 文档。

后端 Python 依赖分两层管理：核心依赖在 `backend/pyproject.toml`，基础设施相关依赖（pymilvus / elasticsearch / neo4j / pdfplumber 等）在 `backend/requirements.txt`。

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

这会启动 PostgreSQL、Milvus、Elasticsearch、Neo4j、Redis。如果暂时不需要 RAG / 记忆 / 知识图谱 / Redis 缓存，可以只启动 PostgreSQL：

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

首次启动时，后端会自动建表并 seed 内置 Agent。启动后查看后端终端的 **Startup Status** 面板，确认各服务连接状态。首次访问需要注册一个账号。

API key 既可以配在 `backend/.env`，也可以在应用的设置面板里配（存入 `user_settings` 表，按用户隔离）。Agent 级别的 key 会覆盖用户级设置。

> 更详细的启动指南见 [QUICKSTART.md](./QUICKSTART.md)。

---

## 基础设施服务

AChat 的基础设施服务通过 Docker Compose 管理，提供两种编排文件：

| 文件 | 用途 |
|---|---|
| `docker-compose.infra.yml` | 仅基础设施（PG/Milvus/ES/Neo4j/Redis/**Phoenix**），前后端在本机运行 |
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

**降级策略**：每个基础设施服务独立 try/except，单个失败不影响其他。Milvus 挂 → 退化为 TF cosine；ES 挂 → 无全文检索；Neo4j 挂 → GraphMemory no-op；Redis 挂 → 退化为同步 DB 读写；Phoenix 不可达 → OTel 缓冲后静默丢弃，不阻断主链路。不配任何基础设施（仅 PostgreSQL）时，核心对话功能完全正常。

| 服务 | 端口 | 不配时的影响 |
|---|---|---|
| PostgreSQL | 5432 | **必需**，后端无法启动 |
| Milvus | 19530 | RAG 向量检索退化；LTM 退化为 TF cosine |
| Elasticsearch | 9200 | RAG 无全文检索 |
| Neo4j | 7474/7687 | GraphMemory no-op；RAG 无图谱检索 |
| Redis | 6379 | 退化为同步 DB 读写（无 KV 缓存，无 Stream write-behind） |
| Phoenix | 6006 / 4317 | 可观测性关闭（`trace_enabled=false`）；OTel 埋点 no-op，无 Trace/Eval 数据 |

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

移动端被设计成通过 LAN 或 Tailscale 连接桌面端的 AChat host。Agent 执行、文件写入、命令执行和 workspace 状态都留在桌面侧。

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
.agenthub-data/worktrees/      # git worktree 隔离
```

---

## 架构

AChat 采用前后端分离架构：

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
│    DocumentService、PromptAssembler、      │
│    HookRegistry (生命周期 Hooks)、          │
│    AuthMiddleware (JWT/CSRF)、              │
│    AsyncDBWriter (Redis Stream write-behind)、│
│    Observability (OTel + Phoenix)         │
│  L2 Agent Platform Adapters               │
│    ClaudeCLI、CodexCLI、Custom、Mock       │
│  L1 Persistence                           │
│    SQLAlchemy、PostgreSQL(22表)、workspace FS │
├──────────────────────────────────────────┤
│  Infrastructure (可选, 独立降级)            │
│    Milvus(向量) · ES(全文) · Neo4j(图谱)   │
│    Redis(缓存+异步写) · Phoenix(可观测性)   │
│    Kafka(事件) · RAG混合检索 · 分层记忆     │
│    知识图谱 · Agent 可观测性与评测           │
└──────────────────────────────────────────┘
```

核心契约是 `StreamEvent`。适配器输出、工具活动、产物创建、待审批、调度状态、用量更新，都先汇入这个事件模型，再通过 SSE 到达前端 UI。工具执行经过 `HookRegistry` 拦截（pre/post），支持审批拦截、自动压缩、检查点保存等可插拔 Hook。SDK Agent 运行时自动合并 9 个 baseline 工具（fs_read/fs_write/fs_edit/fs_list/fs_glob/fs_grep/bash/ask_user/read_attachment），确保所有 custom agent 都具备基础文件操作能力。

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

AChat 假定 LLM 的输出是不可信输入。

- 文件工具把路径解析到会话生效的 workspace 之内。
- Bash 命令在 workspace cwd 内运行。
- 危险的 bash 模式会被拦截（POSIX / Windows 双平台黑名单）。
- 高风险命令可以要求审批。
- 生成的 web app 产物在沙箱 iframe 里渲染（`sandbox="allow-scripts"`，不给 `allow-same-origin`）。
- API key 是本地设置或环境变量；没有任何托管的 key 服务。

系统支持多用户，基于 JWT + bcrypt 密码认证：

- 密码用 bcrypt（cost factor 12）哈希存储。
- JWT 分 access token（1h）和 refresh token（7d），存在 HttpOnly cookie 中。
- `token_version` 字段用于全局吊销（改密码 / logout-all 时 +1）。
- 所有用户数据表通过 `user_id` 列隔离。
- POST / PATCH / DELETE 请求必须携带匹配的 `Origin` header（CSRF 防护）。
- SSE 连接通过 cookie（同源）或 `?token=` query param（跨域 dev）认证。

---

## 已知限制

- 桌面版尚待改造：内嵌 Next 已无后端 API 路由，需改为启动独立 Python 后端。
- 带原生模块的跨平台 Electron 构建，应该通过目标平台机器或 CI 处理。
- Claude / Codex CLI 自带工具层可直接写文件；sandbox 配额只对 AChat 托管的文件工具生效。
- Codex CLI 适配器代码就绪，端到端联调与测试待补；Hermes / OpenClaw / OpenCode 适配器待接入。
- 移动端是伴随客户端，不是独立的 Agent 运行时。
- 基础设施服务（Milvus / ES / Neo4j / Redis）不配时功能降级，但不影响核心对话。
- Hooks 系统的 `tool_approval` Hook 对 CLI 自带工具不生效（CLI 自管审批），仅拦截 AChat 托管工具。

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
