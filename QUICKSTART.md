# AgentHub 快速启动指南

## 环境要求

- **Node.js 20+**
- **pnpm** 包管理器
- **Python 3.11+**

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

## 3. 配置前端指向 Python 后端

项目当前缺失 `src/app/api/` 和 `src/server/` 目录，Next.js 内置 API 路由不存在，因此前端需要连接独立的 Python 后端。

创建 `.env.local`：

```powershell
# 指向 Python FastAPI 后端
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

> `.env.local` 已被 `.gitignore` 忽略，不会提交到 Git。

## 4. 启动 Python 后端

```powershell
cd D:\java\project\bitdance-agenthub-main\backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

> 如果之前在其他路径创建过 `.venv`，请先删除 `.venv` 目录再重新创建。

启动后端服务：

```powershell
uvicorn app.main:app --reload --port 8000
```

后端 API 文档：`http://localhost:8000/docs`

### （可选）配置 AI API Key

在 `backend/` 目录下创建 `.env`：

```env
ANTHROPIC_API_KEY=你的密钥
# 或
OPENAI_API_KEY=你的密钥
```

## 5. 启动前端

在另一个终端窗口中：

```powershell
cd D:\java\project\bitdance-agenthub-main
pnpm dev
```

访问：`http://localhost:3000`

## 常见问题

### Turbopack junction point 错误

如果看到 `failed to create junction point` 错误，删除 `.next` 缓存后重启：

```powershell
Remove-Item -Recurse -Force .next
pnpm dev
```

### 前端 API 404

确认：
1. Python 后端已启动在 `http://localhost:8000`
2. `.env.local` 中配置了 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`
3. 修改 `.env.local` 后已重启 `pnpm dev`

### Python 依赖安装报错

如果提示 `Readme file does not exist`，说明 `backend/README.md` 缺失，创建一个即可：

```powershell
cd backend
echo "# AgentHub Python Backend" > README.md
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `pnpm dev` | 启动前端开发服务 |
| `pnpm lint` | 代码检查 |
| `pnpm test` | 运行单元测试 |
| `uvicorn app.main:app --reload --port 8000` | 启动 Python 后端 |
| `pnpm electron:dev` | 启动 Electron 桌面端 |
| `pnpm mobile:dev` | 启动移动端开发服务 |
