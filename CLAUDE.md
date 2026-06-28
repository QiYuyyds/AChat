# CLAUDE.md — AChat 项目 AI 协作主文档

> 这是 AChat 项目的「项目级 AI 协作约定」。任何 AI 协作工具（Claude Code、Cursor、Codex 等）在本项目工作时**必须**先读此文档，再开始任务。
>
> 本文档与 `openspec/`、`specs/` 配套：CLAUDE.md 定**规则**（怎么做、不做什么），`openspec/specs/` 定 OpenSpec 能力契约，`specs/` 保留编号版详细规格。
>
> **新会话快速上手**：想在不通读代码的前提下建立项目全貌（已实现功能 + 代码地图 + 当前进度），先读根目录 [OVERVIEW.md](./OVERVIEW.md) —— 它定**地图**（做了什么 / 代码在哪），本文档定**规则**。架构全貌见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

---

## 1. 项目背景

**AChat** 是一个多 Agent 协作平台。一句话定位：

> 把多 Agent 协作做成 IM 群聊体验。Agent 是「联系人」，对话是「工作空间」，Orchestrator 是「群里的项目经理」。

### 核心能力

- IM 范式的会话管理（单聊 / 群聊 / 多会话并行）
- 统一适配器层接入 Claude、Custom(OpenAI 兼容) Agent + 自建 Agent
- Orchestrator 自动拆任务、并行调度、聚合结果
- 产物（代码、网页、文档、PPT）内联预览与二次编辑
- 每个会话独立 workspace，Agent 可读写文件、跑命令
- **RAG 混合检索**（Milvus 向量 + Elasticsearch 全文 + Neo4j 知识图谱）
- **分层记忆系统**（短期 / 长期 / 偏好 / 图谱记忆 + 自动固化与衰减）
- **Document + Version 知识库**（全局文档版本化、解析入库、按需召回）

### 运行形态

前后端分离本地运行。前端 Next.js（:3000），后端 Python FastAPI（:8000）；基础设施（PostgreSQL / Milvus / ES / Neo4j）通过 Docker Compose 启动，可全部容器化也可仅远端部署基础设施。所有基础设施服务支持独立降级。

---

## 2. 技术栈（已锁定）

### 前端

| 层 | 选型 | 不选什么 / 为什么 |
|---|---|---|
| 前端框架 | Next.js 16 App Router + React 19 | 不选 Pages Router。Next.js 16 与旧版有较多 breaking change，写代码前查 `node_modules/next/dist/docs/` |
| 语言 | TypeScript（strict 模式） | 不写 `any`，需要时用 `unknown` 再 narrow |
| 样式 | Tailwind CSS v4 + shadcn/ui | 不引入其他 UI 库；shadcn 是「复制组件到本项目」模式 |
| 状态 | Zustand + Immer middleware | 不用 Redux/Recoil/MobX |
| 实时 | SSE（一条全局连接） | 不用 WebSocket |
| 包管理 | pnpm | 不用 npm/yarn（lockfile 唯一） |
| Node 版本 | ≥ 20 | |

### 后端

| 层 | 选型 | 不选什么 / 为什么 |
|---|---|---|
| 后端框架 | FastAPI（Python 3.11+） | 不用 Flask/Django |
| ORM | SQLAlchemy 2.0 async | 不用 Tortoise / Peewee |
| 驱动 | asyncpg（PostgreSQL） | 已从 SQLite 迁移到 PostgreSQL |
| 验证 | Pydantic v2 + pydantic-settings | — |
| AI SDK | `anthropic` · `openai`（Python SDK） | 通过适配器层屏蔽差异 |
| 包管理 | pip + venv（`pyproject.toml`） | 不用 poetry/uv（保持简单） |
| Lint | ruff | 不用 flake8/black（ruff 集成） |
| 测试 | pytest + pytest-asyncio | `asyncio_mode = "auto"` |

### 基础设施（Docker Compose，可降级）

| 服务 | 用途 | 不配时 |
|---|---|---|
| PostgreSQL 16 | 关系型主库 | **必需**，后端无法启动 |
| Milvus v2.4.17 | 向量检索（RAG / LTM） | 退化为 TF cosine |
| Elasticsearch 8.14 | 全文检索（RAG BM25） | 无全文检索 |
| Neo4j 5 | 知识图谱（KGStore / GraphMemory） | GraphMemory no-op |
| Kafka | 事件总线增强（可选） | 用 in-process EventBus |

> 代码风格上，前端用 TypeScript，后端用 Python。改前端代码遵守 TS 规范，改后端代码遵守 Python 规范。两端的共享契约是 `src/shared/` 里的纯类型定义（前端）和 `backend/app/schemas/` 里的 Pydantic 模型（后端），两者保持 camelCase 字段兼容。

---

## 3. 架构核心原则

### 3.1 五层分层（不要跨层调用）

```
L5 UI 组件                     src/components/
L4 State + Transport           src/stores/ + src/lib/ (Zustand store + SSE 客户端)
─── HTTP (REST + SSE) ─── 跨进程边界 ───
L3 Application Services        backend/app/services/ (AgentRunner · Orchestrator · ConversationService · EventBus · ToolExecutor · RAGService · ...)
L2 Agent Platform Adapters     backend/app/adapters/ (Claude / Custom / Mock)
L1 Persistence                 backend/app/db/ (SQLAlchemy + PostgreSQL + workspace 文件系统)
─── 基础设施层 (可选, 独立降级) ───
   Milvus · Elasticsearch · Neo4j · Kafka   backend/app/infra/ + rag/ + memory/ + graph/
```

**铁律**：
- UI **永远不**直接调 LLM SDK，必须经过 L3
- Adapter **永远不**写 DB，它只负责事件流翻译
- 工具执行（ToolExecutor）属 L3，不是 Adapter 的事
- 基础设施服务（Milvus/ES/Neo4j）**永远不**在 L3 服务里直接 new 客户端，必须经过 `infra/factory.py` 统一构建并注入

### 3.2 七个核心实体（详见 `specs/01-core-entities.md`）

`Agent` / `Conversation` / `Message` / `Artifact` / `Workspace` / `Tool` / `AgentRun`

修改任一实体的字段时，**必须同步更新 spec 文档**和 `backend/app/db/models.py`。

### 3.3 统一流式事件（详见 `specs/02-stream-events.md`）

整个系统通过一套 `StreamEvent` 类型粘合：
- L2 Adapter 产生事件
- L3 服务层路由 + 持久化
- L4 SSE 推到前端
- L5 store reducer 应用

事件定义在 `backend/app/schemas/events.py`（后端）和 `src/shared/` （前端），字段 **camelCase** 兼容。**新增 Adapter 或 UI 组件时，事件协议是契约，不可绕开**。

### 3.4 Message = parts 数组，不是字符串

```typescript
message.parts = [
  { type: 'thinking', content: '...' },
  { type: 'tool_use', ... },
  { type: 'text', content: '...' },
  { type: 'artifact_ref', artifactId: '...' },
]
```

**不要**把多种内容塞进一个 markdown 字符串再用正则解析。

### 3.5 Artifact 独立于 Message

产物有自己的生命周期、版本、二次编辑。**不要**把产物内容内联到 message 里。

### 3.6 Orchestrator 是特殊 Agent，不是独立服务

Orchestrator 走同一个 `AgentRunner`，只是多了 `plan_tasks` / `report_task_result` 工具与不同的 system prompt。**不要**为它写独立服务路径。

### 3.7 RAG / 记忆是可选增强，不是硬依赖

RAG 混合检索和分层记忆系统通过 `PromptAssembler` 注入 Agent 上下文，但它们**降级时不应阻断核心对话流**。基础设施不可用时，Agent 仍能正常对话（只是没有知识增强）。

---

## 4. 代码风格

### 4.1 文件 / 目录命名

**前端（TypeScript）**：
- 文件名：`kebab-case.ts`（如 `agent-runner.ts`）
- React 组件文件：`PascalCase.tsx`（如 `ChatWindow.tsx`）
- 测试文件：`*.test.ts` 与被测文件同目录
- 不创建 `index.ts` barrel 文件（除非是 shadcn 风格的 `components/ui/`）

**后端（Python）**：
- 文件名：`snake_case.py`（如 `agent_runner.py`）
- 类名：`PascalCase`（如 `AgentRunner`）
- 测试文件：`test_*.py` 在 `backend/tests/` 下
- 模块导出：`__init__.py` 里用 `__all__` 显式声明

### 4.2 命名约定

| 类型 | 风格 | 例 |
|---|---|---|
| 类型 / 接口 / 类 | PascalCase | `Conversation`, `StreamEvent` |
| 变量 / 函数 | camelCase (TS) / snake_case (Py) | `agentRunner` / `agent_runner` |
| 常量 | UPPER_SNAKE | `MAX_TOKENS`, `WORKSPACE_ROOT` |
| 枚举值（字面量联合） | snake_case 字符串 | `'tool_use'`, `'web_app'` |
| DB 列名 | snake_case | `created_at`, `agent_id` |
| URL 路径 | kebab-case | `/api/conversations/[id]/messages` |
| SSE 事件字段 | camelCase | `artifactId`, `runId` |

### 4.3 不要做

- ❌ 不写 `// TODO` / `# TODO` 不跟进。要么删，要么开 task
- ❌ 不留废代码 / 注释掉的代码块
- ❌ 不为「将来可能用到」加抽象。三处重复才提抽象
- ❌ 不在业务代码里 `console.log` / `print()`（用专门的 logger，或临时调试用完即删）
- ❌ 不写多段 docstring。每个函数最多 1 行注释，且只解释 **why**
- ❌ 不引入新依赖而不在 PR / commit 中说明理由

### 4.4 必须做

- ✅ 异常要有上下文（不要 `throw new Error('failed')` / `raise Exception('failed')`，写清楚是什么 failed）
- ✅ 跨进程边界的输入（API body、LLM 输出）必须 Pydantic / zod 验证
- ✅ 所有 LLM 调用 **必须**支持取消（后端用 `asyncio.Event`，前端概念映射 `AbortSignal`）
- ✅ 涉及文件系统的工具必须经过 Workspace 沙箱（见 5.3）
- ✅ 后端 async 函数调用必须 `await`
- ✅ 后端改完跑 `ruff check .` 和 `pytest`

---

## 5. 安全与沙箱

### 5.1 LLM 输出永远是不可信输入

- LLM 生成的 HTML/JS 在 iframe 渲染时必须 `sandbox="allow-scripts"`（不给 `allow-same-origin`）
- LLM 生成的 SQL / shell 命令必须经过白名单或参数化

### 5.2 Bash 工具黑名单（双平台）

黑名单按宿主平台分支。POSIX（macOS / Linux）与 Windows 各一套。**新增 / 调整规则必须同步 `specs/11-platform.md`「命令黑名单」节并改 `backend/app/utils/` 下的安全模块** —— 黑名单本身是契约，单文档单数据源。

**POSIX 黑名单**（节选，完整列表见 spec 11）：
- `rm -rf /` / `sudo` / `chmod 777 /` / fork bomb / `curl|bash` / `wget|sh` / `eval` / `exec ...`

**Windows 黑名单**（节选，完整列表见 spec 11）：
- `del /F /Q C:\` / `rd /S /Q C:\` / `Remove-Item -Recurse -Force` / `format C:` / `shutdown` / `reg delete` / `iex(iwr ...)` / `Set-ExecutionPolicy Unrestricted` / `bcdedit` / `diskpart`

命令在执行前需要匹配对应平台的黑名单。任何「快速放过」必须在 PR 中说明理由。

### 5.3 Workspace 沙箱

所有 `fs_read` / `fs_write` / `bash` 工具调用：
- 路径必须解析后落在 **effective cwd** 子树内：`workspace.mode === 'local'` 时是 `workspace.boundPath`，否则是 `workspace.rootPath`
- bash 的 cwd 强制为 effective cwd
- **sandbox 模式**：workspace 单目录上限 100MB / 1000 文件（超过拒绝写入）
- **local 模式**：不强制配额（用户用 git 等管理自己的真实项目）；创建会话时已拒过明显敏感目录（`~/.ssh`、`/etc` 等）

### 5.4 API Key 管理

Key 来源按优先级（详见 `backend/app/services/settings_service.py` 与 `backend/app/services/agent_runner.py:buildAdapter_input`）：

1. **`agents.api_key`** — per-agent override（最高优先级；agent 库里单独填）
2. **`app_settings.<provider>_api_key`** — 用户在「设置」面板全局自填，存 `app_settings` 单行表
3. **`backend/.env`** — 环境变量兜底（dev / CI 友好；`config.py` 的 `apply_env_overrides()` 桥接到 `os.environ`）

约束：

- **绝不**在代码中硬编码 key
- **不引入** keychain / safeStorage / 第三方加密存储 —— 本地单用户场景，DB 文件系统权限已经够
- 缺失 key 时，由 adapter 在 SDK 内抛错（不要在启动时拒绝服务，因为用户可能只用其中某些 provider）
- RAG / 记忆系统另有 `EMBEDDING_API_KEY` 和 `LLM_API_KEY` 配置（见 `backend/.env.example`）

---

## 6. AI 协作规则（核心）

这一节是本文档的灵魂。任何 AI 协作工具在本项目工作时必须遵守。

### 6.1 三种工作模式

| 模式 | 何时进入 | 行为 |
|---|---|---|
| **Spec 驱动** | 接到「实现 X」类需求 | 先读 `openspec/project.md` 与 `openspec/specs/` 找对应 capability，再读 `specs/` 细节。spec 缺失时**先写 OpenSpec 变更/规格**，让人确认后再写代码 |
| **修复驱动** | 接到「修 bug」类需求 | 先定位根因（不是症状）。写修复前在 task / PR 说明根因 |
| **探索驱动** | 接到「研究 / 设计 X」类需求 | 不写实现代码，输出 spec / 设计文档 |

### 6.2 必须停下来问的情形

不要自作主张。遇到以下情形必须停下来问人：

- 需要新增依赖
- 需要修改 spec 里定义的接口 / 数据结构
- 需要删除 / 重命名已经被多处引用的符号
- 需要修改安全约束（黑名单、沙箱规则）
- 需要新增 / 修改数据库表结构（`backend/app/db/models.py`）
- 看不懂为什么这段代码这么写（先问，不要重构）
- 用户的请求和某个 spec 冲突

### 6.3 不要做的事

- ❌ 修代码顺手做不相关的「优化」 / 「整理」（每个 PR / commit 一个事）
- ❌ 删除看起来「没用」的代码而不验证有没有外部引用
- ❌ 改 `.env.example` 而不通知（影响所有协作者）
- ❌ 引入新的 LLM SDK / 工具 / 框架而不更新本文档
- ❌ 把多个 spec 的修改塞到一个 PR
- ❌ 后端 async 函数不 await 就调用

### 6.4 输出代码时

- **小步**：每次只解决一个 spec / 一个 task。一次 100 行内能解决就别写 500 行
- **可解释**：每段非平凡逻辑能口头讲清楚为什么这么写
- **可测试**：纯函数能单元测，副作用集中在边界
- **遵守现有模式**：别人怎么写消息渲染，你也怎么写。不要"我觉得换一种更好"
- **双语言意识**：改前端用 TS 规范，改后端用 Python 规范；共享类型改了要两端同步

### 6.5 完成任务的自检清单

提交前自检：

- [ ] 前端改动用 `pnpm typecheck` 过
- [ ] 前端改动用 `pnpm lint` 过
- [ ] 后端改动用 `ruff check .` 过
- [ ] 后端改动用 `pytest` 过
- [ ] 涉及 spec 的修改，spec 文档已同步更新
- [ ] 新增的工具 / 适配器 / 实体在 CLAUDE.md 中能找到对应章节
- [ ] 没有遗留的 `console.log` / `print()` / `TODO` / 注释代码
- [ ] 涉及流式事件的修改，没破坏现有事件契约（`backend/app/schemas/events.py` ↔ `src/shared/`）
- [ ] 涉及 DB schema 的修改，已更新 `backend/app/db/models.py`

---

## 7. 提交规范

### 7.1 Commit 格式

```
<type>(<scope>): <subject>

<body, 可选>
```

`type` ∈ `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `spec`
`scope` 用层名或模块名：`adapter`, `orchestrator`, `ui`, `db`, `rag`, `memory`, `spec` 等

例：
- `feat(rag): add KGStore hybrid retrieval with RRF fusion`
- `fix(orchestrator): correct DAG topological sort for cyclic plans`
- `spec(message-model): add artifact_ref part type`

### 7.2 一个 commit 一件事

- 不要把 spec 修改和实现代码混在一个 commit
- 不要把多个不相关功能混在一个 commit

---

## 8. Specs 与 Skills 索引

### `openspec/`（项目规格契约）

- `project.md` — 项目上下文、技术栈、OpenSpec 与旧 specs 映射
- `specs/core-domain/spec.md` — 核心实体和边界
- `specs/stream-events/spec.md` — StreamEvent 协议
- `specs/message-parts/spec.md` — MessagePart 结构
- `specs/artifacts/spec.md` — Artifact 生命周期
- `specs/adapters/spec.md` — Adapter 契约与 Claude/Custom 边界
- `specs/orchestrator/spec.md` — Orchestrator 调度
- `specs/tools/spec.md` — 工具系统与审批
- `specs/persistence/spec.md` — PostgreSQL/SQLAlchemy 持久化
- `specs/frontend/spec.md` — 前端状态与渲染
- `specs/agent-builder/spec.md` — Agent 创建/编辑
- `specs/platform-security/spec.md` — 平台安全与命令黑名单
- `specs/desktop-electron/spec.md` — Electron 桌面版
- `specs/conversation-context/spec.md` — 跨 run 上下文
- `specs/mobile-companion/spec.md` — 移动伴随 App

### `specs/`（编号版详细规格）

- `01-core-entities.md` — 7 个核心实体的字段定义
- `02-stream-events.md` — StreamEvent 完整事件类型
- `03-message-parts.md` — MessagePart 各类型详解
- `04-artifacts.md` — Artifact 类型与渲染契约
- `05-adapter-interface.md` — AgentPlatformAdapter 接口
- `06-orchestrator-flow.md` — Orchestrator 三阶段工作流
- `07-tools.md` — 内置工具清单与签名
- `08-db-schema.md` — SQLAlchemy schema 与索引
- `09-frontend-architecture.md` — 前端状态结构与事件应用
- `10-agent-builder.md` — 自建 Agent 流程
- `11-platform.md` — 平台抽象（POSIX / Windows shell 选择、双平台黑名单、子进程清理）
- `12-desktop-electron.md` — 桌面版（Electron 打包 DMG / EXE）
- `13-conversation-context.md` — 跨 run 对话历史序列化
- `14-mobile-remote.md` — 移动端伴随 App（Capacitor / Tailscale / 远程审批）
- `15-external-mcp.md` — 外部 MCP 工具接入（设计提案）
- `16-message-search.md` / `16-task-contract-handoff.md` — 消息搜索 / 任务契约交接
- `17-orchestrator-plan-review.md` — Orchestrator 计划审批
- `18-document-knowledge-base.md` — Document + Version 知识库体系

### `skills/`（可复用开发任务模板）

几类「会反复做」的扩展任务，各一份步骤化指南，目录说明见 `skills/README.md`。

- `add-adapter.md` — 新增一个 Adapter（接入新 agent 平台）
- `add-tool.md` — 新增一个工具（LLM 可调用的 function）
- `add-message-part.md` — 新增一种 MessagePart 类型
- `add-artifact-type.md` — 新增一种 Artifact 类型

---

## 9. 文档维护

- specs 与代码冲突时，**以 spec 为准**，先改代码或改 spec（选你认为对的，但要写明）
- 修改架构原则（§3）或安全约束（§5）必须经过讨论，不可单方面提交
- 本文档与 specs 不堆砌历史决策记录，过时内容直接删除（git log 是历史的归宿）
- 基础设施配置变更时同步更新 `backend/.env.example` 和 `docker-compose*.yml`
