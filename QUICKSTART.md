# AChat 快速启动指南

> 本指南帮助你从零搭建 AChat 本地开发环境。项目为前后端分离架构：前端 Next.js + 后端 Python FastAPI + 双 DB 架构（本地 SQLite[WAL] + 远端 PostgreSQL）+ 可选基础设施（Milvus / Neo4j / Phoenix 可观测性后端）。后端还集成了代码图谱智能（CodeGraph 本地运行时）、Obsidian 知识同步、外部 MCP 接入等增强能力。

## 环境要求

- **Node.js 20+**
- **pnpm** 包管理器
- **Python 3.11+**
- **PostgreSQL 16**（或通过 Docker Compose 启动）
- **Docker**（用于启动基础设施服务，可选但推荐）

---

## 1. 安装 pnpm

如果还没有安装 pnpm：

```powershell
npm install -g pnpm
```

## 2. 安装前端依赖

```powershell
cd D:\java\project\bitdance-agenthub-main
pnpm install
```

## 3. 启动基础设施服务（推荐）

AChat 后端采用双 DB 架构：本地 SQLite[WAL] 承载对话热数据，远端 PostgreSQL 承载用户系统与知识/RAG 数据。RAG 混合检索和记忆系统还需要 Milvus、Neo4j（全文检索由 Milvus 原生 BM25 sparse vector 承担）；Phoenix 提供全链路追踪和评测（均可降级，不配也能跑）。

### 方式 A：一键启动全部基础设施（Docker Compose）

```powershell
docker compose -f docker-compose.infra.yml up -d
```

这会启动 PostgreSQL（:5432）、Milvus（:19530）、Neo4j（:7474/:7687）、Phoenix（:6006 Web UI / :4317 OTLP gRPC）。

> ~~Redis~~ 已移除（双 DB 架构下 SQLite 直写 + 进程内 dict TTL 缓存替代）。

### 方式 B：仅启动 PostgreSQL（最小化）

如果你暂时不需要 RAG / 记忆 / 知识图谱，只需 PostgreSQL：

```powershell
docker compose -f docker-compose.infra.yml up -d postgres
```

> Milvus 启动较慢（1-2 分钟），后端内置了重试机制（4 次重试，约 85 秒）。如果看到 Milvus 连接失败的警告，等待片刻重启后端即可。

## 4. 配置前端指向 Python 后端

创建 `.env.local`（项目根目录）：

```powershell
# 指向 Python FastAPI 后端
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

> `.env.local` 已被 `.gitignore` 忽略，不会提交到 Git。

## 5. 安装后端依赖

```powershell
cd D:\java\project\bitdance-agenthub-main\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

> 如果之前在其他路径创建过 `.venv`，请先删除 `.venv` 目录再重新创建（虚拟环境路径移动后需重建）。
>
> 如果提示 `Readme file does not exist`，确认 `backend/README.md` 存在。

## 6. 配置后端环境变量

复制示例配置并填写：

```powershell
cp .env.example .env
```

编辑 `backend/.env`，**最小配置**（仅核心对话功能）：

```env
# 远端数据库（指向 Docker 启动的 PostgreSQL）
DATABASE_URL=postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub
# 本地数据库（对话热数据，默认 SQLite[WAL]）
DATABASE_LOCAL_URL=sqlite+aiosqlite:///./.agenthub-data/local.db
# JWT 密钥（用户认证）
JWT_SECRET=请改为一个随机字符串

# AI API Key（至少配一个）
ANTHROPIC_API_KEY=你的密钥
# 或
OPENAI_API_KEY=你的密钥
# 或
DEEPSEEK_API_KEY=你的密钥
```

**完整配置**（启用 RAG / 记忆 / 知识图谱）：

```env
# ── 基础 ──
DATABASE_URL=postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub
DATABASE_LOCAL_URL=sqlite+aiosqlite:///./.agenthub-data/local.db
JWT_SECRET=请改为一个随机字符串
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
DEEPSEEK_API_KEY=                # ★ 小A Guide Agent 默认 provider 的 key（开箱即用必需）
ARK_API_KEY=

# ── Web 搜索（web_search 工具）──
TAVILY_API_KEY=

# ── Milvus（向量 + BM25 全文检索，留空=禁用）──
MILVUS_HOST=localhost
MILVUS_PORT=19530

# ── Neo4j（知识图谱，留空=禁用）──
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=agenthub-neo4j
ENABLE_GRAPH=true

# ── Embedding（RAG/LTM 语义检索必需）──
EMBEDDING_API_KEY=
EMBEDDING_API_URL=
EMBEDDING_MODEL=

# ── 可观测性（OpenTelemetry + Arize Phoenix）──
TRACE_ENABLED=true           # false = 禁用可观测性（OTel 全 no-op）
PHOENIX_ENDPOINT=http://localhost:4317  # OTLP gRPC endpoint
PHOENIX_UI_URL=http://localhost:6006    # Phoenix Web UI
EVAL_RULE_ENABLED=true       # 在线规则评测（默认开启）
EVAL_JUDGE_ENABLED=false     # 离线 LLM-as-Judge（默认关闭）

# ── 记忆 pipeline（可选）──
# MEMORY_ENABLED=false        # 关闭记忆自动固化 pipeline（节省 API 调用，用于 RAG 测试场景）

# ── Aeval 评测框架（可选，默认关闭）──
# 先安装: pip install "aeval-framework[api,cli]"
# EVAL_HARNESS_ENABLED=true   # 开启后在 /api/eval 挂载独立评测子应用
# EVAL_AGENT_ID=              # 被评 agent 的 ID（必填，缺凭证时评测子应用返回 503）
# EVAL_USER_TOKEN=            # 评测回调 token（留空自动铸造 default 用户 token）
# EVAL_RUN_TIMEOUT=300        # 单次评测运行超时（秒）
# AEVAL_JUDGE_API_KEY=        # LLM 输出质量评分（留空回退 EVAL_LLM_*，再回退 OPENAI_API_KEY）

# ── 代码图谱智能（可选，留空=禁用）──
# CodeGraph 运行时首次使用时会自动下载到 .agenthub-data/runtimes/codegraph/
# 用户也可在 UI 中手动确认下载

# ── Obsidian 同步（可选）──
# 在 UI 的知识库面板中配置 Obsidian vault 路径，无需环境变量

# ── 外部 MCP Server（可选）──
# 在 UI 的设置面板中配置 MCP Server（command / args / env / transport_type）

# ── 小A Guide Agent（可选自定义）──
# 小A 默认走 deepseek provider + DEEPSEEK_API_KEY 兜底，开箱即用
# 如需切换 provider/model/key，配置以下环境变量：
# GUIDE_AGENT_MODEL_PROVIDER=deepseek    # openai-compatible provider 名
# GUIDE_AGENT_MODEL_ID=deepseek-v4-flash # 模型 ID
# GUIDE_AGENT_API_KEY=                   # per-agent key（留空走 DEEPSEEK_API_KEY）
# GUIDE_AGENT_API_BASE_URL=              # 自定义 base URL（留空用 provider 默认）
```

> **降级说明**：Milvus / Neo4j / Embedding / Phoenix 任一不配，后端仍能正常启动和对话，只是对应功能降级（向量检索退化为 TF cosine、无图谱、无语义召回、无 Trace/Eval 数据）。启动时后端会打印状态面板，一目了然。
>
> **Phoenix Web UI**：基础设施启动后访问 `http://localhost:6006` 可查看 Agent 运行的 Trace 瀑布流和 Eval 评分。`trace_enabled=false` 时可观测性全关闭。

## 7. 启动后端服务

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

后端 API 文档：`http://localhost:8000/docs`
健康检查：`http://localhost:8000/health`

启动后查看终端日志中的 **Startup Status** 面板，确认各服务连接状态。

## 8. 启动前端

在另一个终端窗口中：

```powershell
cd D:\java\project\bitdance-agenthub-main
$env:NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"; pnpm dev
```

访问：`http://localhost:3000`

首次启动时，后端会自动建表并 seed 内置 Agent（Orchestrator / PM 小灰 / UI 设计师 / 前端工程师 / Reviewer，以及★ 小A Guide Agent）。首次访问需要注册一个账号（注册页面 `http://localhost:3000/register`），登录后即可开始使用。

**★ 小A 全局悬浮助手**：登录后会在主界面右下角自动展开一个悬浮面板——这是小A，AChat 的管理门面 Agent。你可以用自然语言跟它说「帮我创建一个程序员 Agent」「整理一下我的记忆」「看看最近的会话」等，它会调用 7 个管理工具完成操作，无需手动点 UI。快捷键 `Ctrl+G`（macOS `Cmd+G`）收起/展开面板。小A 开箱即用：默认走 DeepSeek provider，只要配了 `DEEPSEEK_API_KEY` 就能用；也可通过 `GUIDE_AGENT_*` 环境变量切换 provider/model/key。

**创建自定义 Agent**：在 Agent 库中点击「创建 Agent」，选择 4 种角色预设之一（程序员 / 调研员 / 协调者 / 写作），预设自带匹配的 system prompt 和工具推荐。所有 custom agent 自带 9 个基础工具（fs_read/fs_write/fs_edit/fs_list/fs_glob/fs_grep/bash/ask_user/read_attachment），另可勾选 6 个可选工具（write_artifact/deploy_artifact/deploy_workspace/read_artifact/web_search/rag_search）。

**代码图谱智能**：在 Local 模式的会话中，侧栏会显示代码图谱开关。首次启用时会提示下载 CodeGraph 运行时（自动下载到 `.agenthub-data/runtimes/codegraph/`），完成后 Agent 可通过 `code_explore` 工具探索项目代码结构。

---

## 常见问题

### Turbopack junction point 错误

如果看到 `failed to create junction point` 错误，删除 `.next` 缓存后重启：

```powershell
Remove-Item -Recurse -Force .next
pnpm dev
```

### 前端 API 404 / Failed to fetch

1. 确认 Python 后端已启动在 `http://localhost:8000`（访问 `http://localhost:8000/health` 应返回 `{"status":"ok"}`）
2. 确认 `.env.local` 中配置了 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`
3. 修改 `.env.local` 后需重启 `pnpm dev`

### 后端启动报数据库连接错误

1. 确认 PostgreSQL 已启动：`docker compose -f docker-compose.infra.yml ps`
2. 确认 `backend/.env` 中 `DATABASE_URL` 指向正确的地址和端口
3. 如果是远程服务器部署的基础设施，将 `DATABASE_URL` 改为服务器 IP

### Milvus 连接失败

Milvus standalone 启动较慢（1-2 分钟），后端有 4 次重试（约 85 秒）。如果仍失败：
1. 检查 etcd 和 minio 容器是否健康：`docker compose -f docker-compose.infra.yml ps`
2. 确认 `MILVUS_HOST` 和 `MILVUS_PORT` 配置正确
3. 不需要 RAG 时可留空 `MILVUS_HOST` 跳过

### Python 依赖安装报错

1. 确认 Python 版本 ≥ 3.11：`python --version`
2. 确认已激活虚拟环境：`.\.venv\Scripts\Activate.ps1`
3. Windows 下 `requirements.txt` 含特殊字符导致解码失败时，确认文件编码为 UTF-8

### PowerShell 不支持 `&&`

PowerShell 中多命令需用分号 `;` 分隔，不支持 `&&`。例如：

```powershell
# 正确
$env:NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"; pnpm dev

# 错误（&& 不可用）
$env:NEXT_PUBLIC_API_BASE_URL="http://localhost:8000" && pnpm dev
```

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `pnpm dev` | 启动前端开发服务 |
| `pnpm typecheck` | TypeScript 类型检查 |
| `pnpm lint` | ESLint 代码检查 |
| `pnpm test` | Vitest 单元测试 |
| `cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000` | 启动 Python 后端 |
| `cd backend; .\.venv\Scripts\python.exe -m pytest` | 后端测试 |
| `cd backend; .\.venv\Scripts\python.exe -m ruff check .` | 后端 lint |
| `docker compose -f docker-compose.infra.yml up -d` | 启动基础设施 |
| `docker compose -f docker-compose.infra.yml down` | 停止基础设施 |
| `pnpm electron:dev` | 启动 Electron 桌面端 |
| `pnpm mobile:dev` | 启动移动端开发服务 |
