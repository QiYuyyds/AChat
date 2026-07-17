## Why

当前 AChat 桌面端 spec（spec 12 / openspec `desktop-electron`）基于旧 Node.js 全栈架构（Next.js API Routes + better-sqlite3 + Drizzle ORM）编写，与当前 Python FastAPI + PostgreSQL 后端完全不兼容。用户将基础设施部署在远端服务器后，通过浏览器只能绑定服务器上的目录，无法让 Agent 操作本地文件（bash / fs_read / fs_write / fs_edit）。需要重写桌面端架构，使非技术用户能双击安装后绑定本地目录，让 Agent 在用户本机执行工具。

## What Changes

- **重写 Electron 主进程架构**：从"in-process require Next.js standalone"改为双子进程模式——spawn Python FastAPI 子进程 + spawn Next.js 子进程
- **新增嵌入式 Python 分发**：将 Python 3.11 embeddable distribution 打包进安装包，配合预打包 `.whl` 文件实现离线 pip install，用户无需预装 Python
- **新增独立 Node.js runtime**：为 Next.js standalone 提供独立 Node 进程（不使用 ELECTRON_RUN_AS_NODE），避免 ABI 冲突
- **新增首次启动配置向导**：Electron 原生窗口引导用户填写远端基础设施地址（PostgreSQL / Milvus / ES / Neo4j / Redis），存入 `<userData>/data/server.json`
- **重写数据路径策略**：从 SQLite 本地文件改为远端 PostgreSQL 连接，本地 `<userData>/data/` 仅存 workspace 文件和配置
- **新增 preload native file dialog**：通过 preload 暴露 `electronAPI.pickDirectory()` 原生目录选择器，替换 web 版 `/api/fs/listdir` 浏览
- **更新 electron-builder 配置**：新增 `python/`、`wheels/`、`backend/` 目录到打包文件列表，移除 better-sqlite3 ABI 相关逻辑
- **新增多进程生命周期管理**：Python / Next.js / Electron 三个进程的启动探活、异常重启、退出清理

## Capabilities

### New Capabilities
- `desktop-python-runtime`: 嵌入式 Python + wheels 离线安装 + FastAPI 子进程管理——定义 Python 打包格式、依赖安装流程、进程启停与探活
- `desktop-setup-wizard`: 首次启动配置向导——定义远端基础设施连接配置 UI、server.json 格式、连接测试流程
- `desktop-multi-process`: 多进程生命周期管理——定义 Python / Next.js / Electron 三进程启动时序、健康探活、异常重启、退出清理策略

### Modified Capabilities
- `desktop-electron`: 桌面端核心架构从 Node.js 全栈改为 Python + Next.js 双进程；数据路径从 SQLite 本地文件改为远端 PostgreSQL；移除 better-sqlite3 ABI 相关要求；新增 preload native file dialog
- `persistence`: 本地 workspace 路径在桌面端由 `<userData>/data/workspaces/` 决定（通过 `AGENTHUB_DATA_DIR` 环境变量，现有机制已支持），远端 DB 连接在桌面端由 `server.json` 提供
- `platform-security`: 桌面端 boundPath 在用户本机校验（现有 `is_path_safe` / `is_path_within` 逻辑直接复用），新增 Windows Python embed 分发安全约束

## Impact

- **代码**：`electron/` 目录需重写（main.ts / server-bootstrap.ts / paths.ts）；新增 `electron/setup-wizard/`、`electron/python-manager/`、`electron/process-manager/` 模块；`scripts/electron-prebuild.mjs` 需增加 Python wheels + backend 源码拷贝逻辑
- **API**：无变更——前端通过 Next.js rewrites 代理到本地 Python FastAPI，与 web 版完全一致
- **依赖**：新增嵌入式 Python 3.11 分发（~10 MB）；新增预打包 wheels（~80 MB）；新增独立 Node.js runtime（~30 MB）；移除 better-sqlite3 native binding
- **系统**：安装包体积从 ~180 MB 增至 ~230 MB（压缩后），安装后 ~350 MB；首次启动增加 10-20 秒 Python 依赖安装时间
- **构建**：CI/CD 需新增"下载 Python embed + 下载/构建 wheels + 拷贝 backend 源码"步骤；macOS 和 Windows 需分别构建 wheels
