# Design: AChat Desktop — Electron + Python 双进程架构

## 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│  AChat Desktop (Electron App)                                       │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Electron Main Process                                        │  │
│  │                                                                │  │
│  │  1. setupDataDir() → AGENTHUB_DATA_DIR                       │  │
│  │  2. readServerConfig() → server.json                          │  │
│  │  3. ensurePythonDeps() → 首次启动 pip install               │  │
│  │  4. spawn Python FastAPI (127.0.0.1:<random>)                 │  │
│  │  5. spawn Next.js standalone (127.0.0.1:<random>)             │  │
│  │  6. BrowserWindow → loadURL(Next.js)                         │  │
│  │  7. preload: native file dialog                               │  │
│  │  8. 退出时清理子进程                                          │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  远端: PostgreSQL / Milvus / ES / Neo4j / Redis / Phoenix           │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 1. 多进程模型

### 1.1 进程清单

| 进程 | 启动方式 | 监听 | 作用 |
|---|---|---|---|
| Electron Main | 系统启动 | — | 主进程：生命周期管理、BrowserWindow、preload |
| Python FastAPI | `spawn` | `127.0.0.1:<random>` | 全部业务逻辑 + 工具执行 |
| Next.js Standalone | `spawn` | `127.0.0.1:<random>` | 前端 SSR + `/api/*` rewrite 到 Python |
| Node.js Runtime | 随 Next.js | — | Next.js standalone 的运行时 |

### 1.2 启动时序

```
t=0ms    Electron main.ts 启动
         ├─ setupDataDir()
         ├─ readServerConfig() → 读 server.json
         ├─ generateJwtSecret() → 首次生成, 后续复用
         └─ 首次启动: ensurePythonDeps()

t=50ms   并行 spawn 两个子进程:

         [Python]  python -m uvicorn app.main:app
                   --host 127.0.0.1 --port <random>
                   环境变量注入: (见 §2)

         [Next.js] node standalone/server.js
                   --port <random>
                   NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:<python-port>
                   HOSTNAME=127.0.0.1

t=500ms  探活 Python: GET /health → 200
t=1000ms 探活 Next.js: GET / → 200
         (每 200ms 重试，最多 15s，超时弹窗报错)

t=1200ms BrowserWindow 创建 + loadURL
t=2000ms 用户看到主界面
```

### 1.3 进程通信

**不引入 Electron IPC**——所有通信走 HTTP/SSE，与 web 版完全一致：

```
BrowserWindow (Renderer)
  → fetch('/api/xxx')
    → Next.js rewrite
      → http://127.0.0.1:<python-port>/api/xxx
        → Python FastAPI 处理
          → SSE 回传
```

**Next.js rewrites 配置**（`next.config.ts`）：

```ts
async rewrites() {
  return [
    { source: '/api/:path*', destination: `${BACKEND_URL}/api/:path*` },
    { source: '/deployments/:path*', destination: `${BACKEND_URL}/deployments/:path*` },
  ]
}
```

`BACKEND_URL` 在桌面端由环境变量 `NEXT_PUBLIC_API_BASE_URL` 指向 Python 端口。

### 1.4 进程健康检查与重启

```
探活策略:
  Python: GET /health, 200 = 健康
  Next.js: GET /, 200 = 健康
  间隔: 每 30 秒
  连续失败 3 次 → 自动重启该子进程
  重启失败 2 次 → 弹窗提示用户"服务异常，请重启 AChat"

异常处理:
  Python crash → Electron 检测到 exit code ≠ 0 → 自动重启
  Next.js crash → 同上
  Electron crash → OS 级别，无法处理（用户手动重启 App）
```

### 1.5 退出清理

```
app.on('before-quit'):
  1. SIGTERM Python 子进程
  2. SIGTERM Next.js 子进程 (Windows: taskkill /F /T /PID)
  3. 等 3 秒
  4. 未退 → SIGKILL (Windows: taskkill /F /T /PID)
  5. app.quit()
```

---

## 2. 环境变量注入

Python 子进程的环境变量从两个来源合并：

| 来源 | 变量 | 说明 |
|---|---|---|
| server.json | `DATABASE_URL`, `MILVUS_HOST`, `MILVUS_PORT`, `ES_ADDRESSES`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `REDIS_URL`, `PHOENIX_ENDPOINT` | 远端基础设施配置 |
| Electron 固定 | `AGENTHUB_DATA_DIR`, `JWT_SECRET`, `HOST`, `PORT`, `CORS_ORIGINS` | 本地配置 |

**关键**：`AGENTHUB_DATA_DIR` 决定本地 workspace 路径和配置文件存放位置。Python 进程读取此环境变量（`backend/app/config.py` 已支持 `WORKSPACE_ROOT` / `DATA_DIR`）。

**`JWT_SECRET`**：首次启动时生成随机值，存入 `<userData>/data/jwt_secret.txt`，后续启动复用。确保桌面端 JWT 在重启后仍然有效。

---

## 3. 嵌入式 Python 分发

### 3.1 目录结构

```
<appRoot>/python/                     ← 打包进安装包
  ├─ python.exe (Windows) / python (macOS)
  ├─ python311.dll / libpython3.11.dylib
  ├─ python311.zip (标准库)
  └─ Lib/
      └─ site-packages/               ← pip install 目标 (首次启动时填充)
```

### 3.2 下载来源

| 平台 | 包 | URL |
|---|---|---|
| Windows x64 | `python-3.11.x-embed-amd64.zip` | https://www.python.org/ftp/python/3.11.x/ |
| macOS arm64 | 从 python.org 官方 installer 提取 framework | 构建时脚本处理 |
| macOS x64 | 同上 | 构建时脚本处理 |

### 3.3 embed Python 注意事项

Windows embed 分发包默认不包含 `pip`。需要在构建时额外放入 `get-pip.py` 或预装 pip 到 `Lib/site-packages/`。

macOS 没有官方 embed 包，构建脚本需要从 CPython 源码或 framework 分发中裁剪出最小 runtime。

---

## 4. Wheels 离线安装

### 4.1 构建时收集

```bash
# CI/CD 构建步骤
pip download -r backend/requirements.txt \
  -d dist/wheels/ \
  --platform win_amd64 \
  --python-version 311 \
  --only-binary=:all:
```

macOS 对应 `--platform macosx_11_0_arm64` / `macosx_11_0_x86_64`。

### 4.2 首次启动安装

```bash
python/python.exe -m pip install \
  --no-index \
  --find-links=python/wheels/ \
  -r backend/requirements.txt \
  --target=python/Lib/site-packages/
```

安装约 10-20 秒（纯本地磁盘 I/O），显示进度条。

### 4.3 安装完成标记

安装成功后写入 `<userData>/data/.pip_installed` 标记文件。后续启动检测到此文件则跳过安装。

---

## 5. 首次启动配置向导

### 5.1 向导流程

```
Step 1: 欢迎
  "欢迎使用 AChat 桌面端"
  "需要配置远端服务器连接信息"

Step 2: 服务器配置
  PostgreSQL 地址:  [postgresql://user:pass@host:5432/agenthub]
  Milvus 地址:      [host:19530]
  Elasticsearch:    [http://host:9200]
  Neo4j:            [bolt://host:7687]
  Neo4j 密码:       [________________]
  Redis:            [redis://host:6379]
  Phoenix:          [http://host:4317]

  [测试连接]  → 并发检测所有服务可达性，显示 ✓/✗

Step 3: 完成 → 保存 server.json → 启动 Python 子进程
```

### 5.2 server.json 格式

```json
{
  "version": 1,
  "databaseUrl": "postgresql+asyncpg://user:pass@host:5432/agenthub",
  "milvusHost": "host",
  "milvusPort": 19530,
  "esAddresses": "http://host:9200",
  "neo4jUri": "bolt://host:7687",
  "neo4jUser": "neo4j",
  "neo4jPassword": "xxx",
  "redisUrl": "redis://host:6379",
  "phoenixEndpoint": "http://host:4317",
  "phoenixUiUrl": "http://host:6006"
}
```

存放位置: `<userData>/data/server.json`

### 5.3 配置向导实现

使用 Electron BrowserWindow 加载本地 HTML（不依赖 Next.js，因为 Next.js 尚未启动）。向导窗口与主窗口不同——无边框、固定尺寸、居中显示。

完成后销毁向导窗口，启动 Python + Next.js，创建主 BrowserWindow。

---

## 6. Preload Native File Dialog

### 6.1 暴露 API

```ts
// preload.ts
contextBridge.exposeInMainWorld('electronAPI', {
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  isDesktop: () => true,
})
```

```ts
// main.ts
ipcMain.handle('pick-directory', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory'],
  })
  return result.canceled ? null : result.filePaths[0]
})
```

### 6.2 前端适配

前端检测 `window.electronAPI?.isDesktop()` 来决定用原生 dialog 还是 web 版 `/api/fs/listdir`。

---

## 7. 安装包结构

```
AChat-0.1.0-setup.exe / AChat-0.1.0.dmg
├─ electron/                    ← Electron 主进程 + preload
│   ├─ main.js
│   ├─ preload.js
│   ├─ paths.js
│   └─ setup-wizard/            ← 配置向导 HTML/CSS/JS
├─ python/                      ← 嵌入式 Python 3.11
│   ├─ python.exe / python
│   ├─ python311.dll / .dylib
│   ├─ python311.zip
│   └─ Lib/site-packages/       ← (首次启动后填充)
├─ wheels/                      ← 预打包 .whl (安装后可删)
├─ backend/                     ← Python 源码
│   ├─ app/
│   ├─ requirements.txt
│   └─ pyproject.toml
├─ next-standalone/             ← Next.js standalone
│   ├─ server.js
│   ├─ .next/
│   └─ node_modules/ (minimal)
├─ node/                        ← 独立 Node.js runtime
│   └─ node.exe / node
└─ config/
    └─ server.json.example
```

---

## 8. 构建流程

### 8.1 开发模式 (`pnpm electron:dev`)

```
1. pnpm dev (Next.js dev server, 纯 Node)
2. Python venv + uvicorn app.main:app (手动或脚本启动)
3. tsc -w -p electron/tsconfig.json
4. wait-on → spawn Electron (AGENTHUB_DEV=1)
   → loadURL(http://localhost:3000)
```

### 8.2 构建模式 (`pnpm electron:build`)

```
1. 构建 Python wheels:
   pip download -r backend/requirements.txt -d dist/wheels/ --only-binary=:all:

2. 下载嵌入式 Python:
   scripts/download-python-embed.mjs → dist/python/

3. 构建前端:
   pnpm build (Next.js standalone output)

4. 预构建:
   pnpm electron:prebuild
   → 拷贝 static/public 到 standalone
   → 补齐 standalone 依赖
   → 清理 broken symlinks

5. 编译 Electron:
   pnpm electron:tsc → dist-electron/

6. 组装安装包目录:
   scripts/assemble-dist.mjs
   → 拷贝 python/ + wheels/ + backend/ + node/ + next-standalone/ + electron/

7. electron-builder
   → release/AChat-<ver>-setup.exe
   → release/AChat-<ver>.dmg
```

---

## 9. 数据一致性

### 9.1 桌面端与网页端共享同一数据库

用户在桌面端和浏览器端使用同一账号登录，看到同一份会话数据。

- 桌面端 Python FastAPI → 远端 PostgreSQL
- 浏览器 → 远端 FastAPI → 同一个 PostgreSQL
- SSE 事件流在两端独立订阅

### 9.2 Workspace 文件

- 桌面端 local 模式：workspace 文件在用户本地 `<userData>/data/workspaces/`
- 网页端 sandbox 模式：workspace 文件在远端服务器

两端创建的对话都存在于同一个数据库，但从不同端查看时，local 模式的 workspace 文件只在本机可访问。

---

## 10. 升级策略

开发者修改代码 → 重新 `pnpm electron:build` → 发布新安装包 → 用户下载安装。

安装时保留 `<userData>/data/` 目录（server.json / workspace / jwt_secret.txt / site-packages），仅替换应用代码。

Python 依赖升级：新版 `requirements.txt` 如有变更，安装包内的 wheels 相应更新；首次启动检测到 `.pip_installed` 版本不匹配时自动重新安装。
