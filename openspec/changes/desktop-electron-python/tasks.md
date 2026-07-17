# Tasks: AChat Desktop — Electron + Python 双进程架构

## Phase 0: 基础设施准备

### Task 0.1: 创建嵌入式 Python 下载脚本
- **What**: 编写 `scripts/download-python-embed.mjs`，从 python.org 下载 Python 3.11 embeddable distribution（Windows x64 / macOS arm64 / macOS x64）
- **Files**: `scripts/download-python-embed.mjs`
- **Spec**: desktop-python-runtime §"Python runtime SHALL be embedded"
- **Acceptance**: 脚本执行后在 `dist/python/` 下生成完整的 embed Python 目录

### Task 0.2: 创建 wheels 收集脚本
- **What**: 编写 `scripts/collect-wheels.mjs`，调用 `pip download -r backend/requirements.txt -d dist/wheels/ --only-binary=:all:` 收集所有依赖的 wheel 文件
- **Files**: `scripts/collect-wheels.mjs`
- **Spec**: desktop-python-runtime §"Python dependencies SHALL be pre-downloaded as wheel files"
- **Acceptance**: 脚本执行后 `dist/wheels/` 包含所有 requirements.txt 的 wheel 文件

### Task 0.3: 创建安装包组装脚本
- **What**: 编写 `scripts/assemble-dist.mjs`，将 python/ + wheels/ + backend/ + node/ + next-standalone/ + electron/ 组装到 `dist/achat-desktop/` 目录
- **Files**: `scripts/assemble-dist.mjs`
- **Spec**: desktop-electron §"Desktop packaging SHALL include Python embed + wheels + backend source"
- **Acceptance**: 组装后的目录结构符合 design.md §7 的定义

## Phase 1: Electron 主进程重写

### Task 1.1: 重写 electron/paths.ts
- **What**: 修改 `setupDataDir()` 逻辑，保持 `AGENTHUB_DATA_DIR` 设置到 `<userData>/data`，移除 SQLite 相关逻辑
- **Files**: `electron/paths.ts`
- **Spec**: desktop-electron §"Desktop data SHALL live in userData"
- **Acceptance**: 打包后 `AGENTHUB_DATA_DIR` 指向 `<userData>/data`

### Task 1.2: 新增 electron/server-config.ts
- **What**: 创建 `readServerConfig()` 函数，从 `<userData>/data/server.json` 读取远端基础设施配置，返回环境变量对象
- **Files**: `electron/server-config.ts`
- **Spec**: desktop-setup-wizard §"Configuration SHALL be persisted as server.json"
- **Acceptance**: 函数正确解析 server.json 并返回 `DATABASE_URL` / `MILVUS_HOST` 等环境变量

### Task 1.3: 新增 electron/jwt-secret.ts
- **What**: 创建 `ensureJwtSecret()` 函数，首次启动生成随机 secret 并存入 `<userData>/data/jwt_secret.txt`，后续复用
- **Files**: `electron/jwt-secret.ts`
- **Spec**: desktop-python-runtime §"JWT_SECRET is generated on first launch"
- **Acceptance**: 首次启动生成 secret 并写入文件；后续启动读取复用

### Task 1.4: 新增 electron/python-manager.ts
- **What**: 创建 Python 子进程管理模块：
  - `ensurePythonDeps()`: 检测 `.pip_installed` 标记，不存在则执行 `pip install --no-index --find-links=wheels/`
  - `spawnPython(env)`: spawn `python -m uvicorn app.main:app` 并注入环境变量
  - `getPythonPort()`: 返回 Python 进程的监听端口
- **Files**: `electron/python-manager.ts`
- **Spec**: desktop-python-runtime §"First launch SHALL install Python dependencies", §"Python subprocess SHALL be spawned with correct environment"
- **Acceptance**: 首次启动自动安装依赖；Python 子进程启动后可响应 `/health` 请求

### Task 1.5: 新增 electron/nextjs-manager.ts
- **What**: 创建 Next.js 子进程管理模块：
  - `spawnNextJs(pythonPort)`: spawn 独立 Node.js 进程运行 `next-standalone/server.js`，传入 `NEXT_PUBLIC_API_BASE_URL`
  - `getNextJsPort()`: 返回 Next.js 进程的监听端口
- **Files**: `electron/nextjs-manager.ts`
- **Spec**: desktop-multi-process §"Next.js subprocess SHALL listen on a random port"
- **Acceptance**: Next.js 子进程启动后可响应 `GET /` 请求；`/api/*` 请求被正确代理到 Python

### Task 1.6: 新增 electron/process-health.ts
- **What**: 创建进程健康检查模块：
  - `startHealthChecks()`: 启动定时健康探活（每 30 秒）
  - `onUnhealthy()`: 连续 3 次失败后触发自动重启
  - 写入 PID 文件用于孤儿进程检测
- **Files**: `electron/process-health.ts`
- **Spec**: desktop-multi-process §"Health probes SHALL monitor subprocesses continuously", §"PID files SHALL be written for subprocess tracking"
- **Acceptance**: 手动 kill Python 子进程后，Electron 自动重启；PID 文件正确写入和清理

### Task 1.7: 新增 electron/process-cleanup.ts
- **What**: 创建进程退出清理模块：
  - `cleanupAll()`: SIGTERM → 等待 3s → SIGKILL
  - Windows 下使用 `taskkill /F /T /PID`
  - `killOrphans()`: 启动时检测并清理上次未正常退出的子进程
- **Files**: `electron/process-cleanup.ts`
- **Spec**: desktop-multi-process §"Application exit SHALL clean up all subprocesses", §"PID files SHALL be written for subprocess tracking"
- **Acceptance**: 关闭 App 后所有子进程退出；强制杀 App 后下次启动清理孤儿进程

### Task 1.8: 重写 electron/main.ts
- **What**: 重写主进程入口：
  1. `app.requestSingleInstanceLock()`
  2. `setupDataDir()`
  3. `readServerConfig()` 或启动 setup wizard
  4. `ensureJwtSecret()`
  5. `ensurePythonDeps()`
  6. `spawnPython(env)` + `spawnNextJs(pythonPort)`
  7. 健康探活通过后创建 BrowserWindow
  8. `app.on('before-quit')` → `cleanupAll()`
- **Files**: `electron/main.ts`
- **Spec**: desktop-multi-process §"Processes SHALL start in a defined order"
- **Acceptance**: 打包后 App 双击启动 → 配置向导（首次）→ Python + Next.js 启动 → 主界面显示

## Phase 2: 配置向导

### Task 2.1: 创建配置向导 UI
- **What**: 创建 `electron/setup-wizard/` 目录，包含独立的 HTML/CSS/JS 文件。表单字段：PostgreSQL URL（必填）、Milvus、ES、Neo4j、Redis、Phoenix（选填）。"测试连接"按钮并发检测可达性
- **Files**: `electron/setup-wizard/index.html`, `electron/setup-wizard/styles.css`, `electron/setup-wizard/app.js`
- **Spec**: desktop-setup-wizard §"Setup wizard SHALL collect infrastructure connection details", §"Setup wizard SHALL test connectivity"
- **Acceptance**: 向导窗口正确渲染表单；点击"测试连接"显示 ✓/✗；PostgreSQL 必须通过才能继续

### Task 2.2: 配置向导 IPC 通信
- **What**: 在 main.ts 中注册 IPC handlers：
  - `test-connection`: 接收连接参数，尝试 TCP/HTTP 连接，返回成功/失败
  - `save-config`: 将配置写入 `server.json`
  - `finish-setup`: 关闭向导窗口，启动主流程
- **Files**: `electron/main.ts` (IPC handlers)
- **Spec**: desktop-setup-wizard §"Configuration SHALL be persisted as server.json"
- **Acceptance**: 配置正确写入 `server.json`；向导完成后主流程正常启动

### Task 2.3: 设置页面添加"重新配置服务器"入口
- **What**: 在前端设置页面添加"服务器配置"区块，点击"重新配置"按钮通过 IPC 打开向导窗口（预填当前值）
- **Files**: `src/components/settings-panel.tsx` (或相应组件)
- **Spec**: desktop-setup-wizard §"User SHALL be able to re-open the setup wizard"
- **Acceptance**: 从设置页面打开向导 → 修改配置 → 保存后 Python 子进程重启

## Phase 3: Preload & 前端适配

### Task 3.1: 创建 electron/preload.ts
- **What**: 实现 preload 脚本，暴露 `electronAPI.pickDirectory()` 和 `electronAPI.isDesktop()`
- **Files**: `electron/preload.ts`
- **Spec**: desktop-electron §"Desktop SHALL expose native file dialog via preload"
- **Acceptance**: 渲染进程中 `window.electronAPI.pickDirectory()` 调用弹出原生目录选择对话框

### Task 3.2: 前端目录选择器适配
- **What**: 修改前端"绑定目录"的目录选择逻辑：检测 `window.electronAPI?.isDesktop()` → 桌面端用 native dialog → web 版保持 `/api/fs/listdir`
- **Files**: `src/components/` 相关组件
- **Spec**: desktop-electron §"Frontend detects desktop mode"
- **Acceptance**: 桌面端弹出原生文件对话框；网页版保持现有 web 目录浏览器

## Phase 4: 构建与打包

### Task 4.1: 更新 electron-builder 配置
- **What**: 修改 `package.json` 的 `build` 字段：
  - `files` 新增: `python/**`, `wheels/**`, `backend/**`, `node/**`, `next-standalone/**`
  - `files` 移除: 不再需要 better-sqlite3 相关的 asarUnpack
  - `asarUnpack`: 保留 `.next/standalone/**`，新增 `python/**`, `backend/**`, `wheels/**`, `node/**`
  - `npmRebuild: false` 保留
- **Files**: `package.json`
- **Spec**: desktop-electron §"Desktop packaging SHALL include Python embed + wheels + backend source"
- **Acceptance**: `pnpm electron:build` 成功输出安装包，包含所有必要文件

### Task 4.2: 更新构建脚本流程
- **What**: 修改 `scripts/electron-prebuild.mjs`：
  - 新增: 拷贝 `dist/python/` → 组装目录
  - 新增: 拷贝 `dist/wheels/` → 组装目录
  - 新增: 拷贝 `backend/` 源码 → 组装目录
  - 新增: 拷贝 Node.js runtime → 组装目录
  - 移除: better-sqlite3 ABI 相关逻辑
- **Files**: `scripts/electron-prebuild.mjs`
- **Spec**: desktop-electron §"Desktop packaging SHALL NOT include better-sqlite3 native bindings"
- **Acceptance**: 构建脚本执行后组装目录完整，不包含 better-sqlite3

### Task 4.3: macOS Python embed 适配
- **What**: 编写 `scripts/prepare-macos-python.sh`，从 CPython 官方 macOS installer 或 framework 分发中裁剪出最小 Python runtime
- **Files**: `scripts/prepare-macos-python.sh`
- **Spec**: desktop-python-runtime §"Python runtime SHALL be embedded" (macOS scenario)
- **Acceptance**: macOS 安装包内 `python/bin/python3.11` 可执行，能运行 `uvicorn`

### Task 4.4: 更新 electron/tsconfig.json
- **What**: 确保新增的 TypeScript 文件（python-manager.ts / nextjs-manager.ts / process-health.ts / process-cleanup.ts / server-config.ts / jwt-secret.ts）在编译范围内
- **Files**: `electron/tsconfig.json`
- **Acceptance**: `pnpm electron:tsc` 编译成功，所有新文件输出到 `dist-electron/`

## Phase 5: 集成测试与验收

### Task 5.1: Windows 打包验收
- **What**: 在 Windows 上完整执行 `pnpm electron:build`，安装并验证：
  - 首次启动显示配置向导
  - 填写远端 PG 地址后 Python 启动成功
  - 创建对话 → 选择绑定本地目录 → Agent 能读写本地文件
  - bash 工具在本地执行
  - 关闭 App 后所有子进程退出
- **Spec**: 全部
- **Acceptance**: 上述所有验证点通过

### Task 5.2: macOS 打包验收
- **What**: 在 macOS 上完整执行 `pnpm electron:build`，验证同 Task 5.1 的所有验证点
- **Spec**: 全部
- **Acceptance**: 上述所有验证点通过

### Task 5.3: 更新 spec 12 文档
- **What**: 将 `specs/12-desktop-electron.md` 重写为基于 Python 双进程架构的版本，标记所有基于旧 Node.js 全栈的章节为已废弃
- **Files**: `specs/12-desktop-electron.md`
- **Spec**: desktop-electron (delta spec)
- **Acceptance**: spec 12 文档与实际实现一致

### Task 5.4: 更新 openspec/desktop-electron/spec.md
- **What**: 将主 spec 更新为新的 requirements，移除 better-sqlite3 相关要求，新增 Python 运行时和配置向导要求
- **Files**: `openspec/specs/desktop-electron/spec.md`
- **Acceptance**: 主 spec 与 delta spec 一致
