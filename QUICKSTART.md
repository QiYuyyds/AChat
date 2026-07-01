# AChat 快速启动指南

> 本指南帮助你从零搭建 AChat 本地开发环境。项目为前后端分离架构：前端 Next.js + 后端 Python FastAPI + PostgreSQL 主库 + 可选基础设施（Milvus / Elasticsearch / Neo4j）。

## 环境要求

- **Node.js 20+**
- **pnpm** 包管理器
- **Python 3.11+**
- **PostgreSQL 16**（或通过 Docker Compose 启动）
- **Docker**（用于启动基础设施服务，可选但推荐）
- **Codex CLI**（可选，用于 Codex CLI adapter agent）：`npm install -g @openai/codex`，安装后运行 `codex auth` 完成认证

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

AChat 后端依赖 PostgreSQL 作为主库。RAG 混合检索和记忆系统还需要 Milvus、Elasticsearch、Neo4j（可降级，不配也能跑）。

### 方式 A：一键启动全部基础设施（Docker Compose）

```powershell
docker compose -f docker-compose.infra.yml up -d
```

这会启动 PostgreSQL（:5432）、Milvus（:19530）、Elasticsearch（:9200）、Neo4j（:7474/:7687）。

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
# 数据库（指向 Docker 启动的 PostgreSQL）
DATABASE_URL=postgresql+asyncpg://agenthub:agenthub@localhost:5432/agenthub

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
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
ARK_API_KEY=

# ── Web 搜索（web_search 工具）──
TAVILY_API_KEY=

# ── Milvus（向量检索，留空=禁用）──
MILVUS_HOST=localhost
MILVUS_PORT=19530

# ── Elasticsearch（全文检索，留空=禁用）──
ES_ADDRESSES=http://localhost:9200

# ── Neo4j（知识图谱，留空=禁用）──
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=agenthub-neo4j
ENABLE_GRAPH=true

# ── Embedding（RAG/LTM 语义检索必需）──
EMBEDDING_API_KEY=
EMBEDDING_API_URL=
EMBEDDING_MODEL=
```

> **降级说明**：Milvus / ES / Neo4j / Embedding 任一不配，后端仍能正常启动和对话，只是对应功能降级（向量检索退化为 TF cosine、无全文检索、无图谱、无语义召回）。启动时后端会打印状态面板，一目了然。

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

首次启动时，后端会自动建表并 seed 内置 Agent（Orchestrator / PM 小灰 / UI 设计师 / 前端工程师 / Reviewer）。

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
