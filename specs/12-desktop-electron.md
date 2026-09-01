# Spec 12 — 桌面版（Electron）v2

> **v2**（OpenSpec change `add-desktop-runtime`，2026-09）。TS 后端时代 v1 的进程模型、DB bootstrap、better-sqlite3 ABI 舞步、`@openai/codex-sdk` / claude-agent-sdk 打包要求全部失效并已删除。现在的桌面 = **Electron 壳 + in-process Next 前端 + Python sidecar 业务后端**。
>
> 源文件：`electron/`（main / preload / paths / server-bootstrap）、`scripts/prepare-python-runtime.mjs`、`scripts/electron-prebuild.mjs`、`backend/requirements-desktop.txt`、`package.json`（build 字段）。

---

## 1. 目标与范围

**做**：
- `pnpm electron:build` 一键产出 Windows NSIS（x64）与 macOS DMG（arm64）
- 双击即用：**不要求用户机器安装 Python / Node / 任何 CLI**
- 桌面强制登录（云端账号，经本地代理）+ 单库 SQLite 全本地数据
- 目录绑定桌面化：OS 原生 picker / 拖拽文件夹 / 最近项目一键重绑
- bash 审批流是一级桌面验证项（Agent 跑在用户真实目录上）

**不做 / 推迟**：自动更新、代码签名（首启 SmartScreen/Gatekeeper 引导文档代替）、托盘/全局快捷键、移动伴随端激活、web ↔ 桌面数据同步、桌面直连云端 PG、Tauri 迁移、统计上报端点（独立 change）。

---

## 2. 进程模型

```
Electron main (Node)
├── BrowserWindow (renderer, sandbox)
│     └── 加载 http://127.0.0.1:<ephemeral> ← in-process Next standalone
│           ├── 前端渲染 + 静态资源
│           └── 同源 /api/* rewrite ──→ http://127.0.0.1:8000 (构建期固化)
└── Python sidecar 子进程
      └── <userData>/python-runtime/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

| 进程 | 职责 | 源码 |
|---|---|---|
| Electron main | 窗口、单实例锁、sidecar 生命周期、原生 dialog、导航防护 | `electron/main.ts` |
| Next standalone | **仅前端**渲染 + 同源 rewrite 转发；无 API routes、无 SDK 运行时 | `.next/standalone`（in-process require） |
| Python sidecar | 全部业务：conversations / runs / adapters / RAG 降级路径 | `backend/app` + embedded CPython |

Next standalone 的角色边界（design D1）：`require(server.js)` 在 main 进程内启动（`asarUnpack` 解出的真实路径，见 §8）；server 探活 `HEAD /`（15s）。**禁止**期望 Next 侧存在任何 API routes 或 agent SDK。

---

## 3. 端口与单实例

- **sidecar 固定 `127.0.0.1:8000`**：Next 的 rewrite destination 在构建期固化进 routes-manifest，运行时不可改写（design D3）。前端端口走系统分配 ephemeral port，避免与 dev :3000 冲突。
- 单实例锁（`requestSingleInstanceLock`）排除自冲突；二次启动 focus 已有窗口。
- 8000 被其他程序占用 → 启动前探测（bind 测试）失败 → 错误界面给出 `netstat -ano | findstr :8000` 排查引导，**绝不白屏**。
- 移动伴随端开启时前端按既有语义绑 `0.0.0.0`（companion.json 配置）；sidecar 恒 loopback（伴随端共存细节留待伴随端 change）。

---

## 4. sidecar 生命周期契约（`electron/server-bootstrap.ts`）

| 阶段 | 行为 |
|---|---|
| spawn | `python -m uvicorn app.main:app`，cwd=backend 源码目录；env 注入见 §6/§7 |
| 探活 | `GET /health` 每 250ms，超时 **30s** 判启动失败 → StartupError |
| 崩溃重启 | 意外退出自动重启，**60s 窗口内上限 3 次**；超限停止并呈现错误界面；恢复成功 reload 窗口 |
| 优雅退出 | `before-quit` → 温和终止（Windows `taskkill /T` 无 `/F`）→ **5s 宽限** → `taskkill /F /T` 强杀进程树，不遗留孤儿进程 |

sidecar env 注入（spawn 时）：
- `AGENTHUB_DESKTOP=1`、`AGENTHUB_DATA_DIR=<userData>/data`
- `DATABASE_URL=sqlite+aiosqlite:///<dataDir>/agenthub.db`、`DATABASE_LOCAL_URL=""`（单库）
- `JWT_SECRET`（打包环境无 .env：首启生成 48B 随机密钥持久化 `<dataDir>/jwt-secret`，重启后会话不失效）
- `MILVUS_HOST=""`、`NEO4J_URI=""`、`KAFKA_BROKERS=""`（桌面默认降级路径）
- `PYTHONDONTWRITEBYTECODE=1`（只读安装目录防 `__pycache__`）、`PYTHONNOUSERSITE=1`
- `AGENTHUB_CLOUD_API_URL`（来自 shell env（dev 覆盖）或 `resources/cloud-config.json`（打包））

dev 模式（`AGENTHUB_DEV=1`）sidecar 用 `backend/.venv` 的解释器 + 仓库 `backend/` cwd；`AGENTHUB_DATA_DIR` 指向仓库 `.agenthub-data-desktop`（与 web dev 的 `.agenthub-data` 隔离——单库表结构与双库 FK 重建不能混文件）。

---

## 5. Python 运行时分发（design D2）

- **构建期**（`scripts/prepare-python-runtime.mjs`）：下载钉死的 python-build-standalone `cpython-3.12.14+20260901`（sha256 对照官方 SHA256SUMS）→ 系统解包 → `pip install --no-compile -r backend/requirements-desktop.txt` → 裁剪 test/idlelib/__pycache__ → 打包 `resources/python-runtime/python-runtime-win32-x64.zip` / `python-runtime-darwin-arm64.tar.gz`（实测 win 96.8MB）。
- `requirements-desktop.txt` 与 `pyproject.toml` dependencies 同步维护；OCR / milvus / neo4j / asyncpg 刻意不装（lazy import + 独立降级）。
- Windows 构建机必须用 `System32\tar.exe`（PATH 里的 Git GNU tar 会把 `D:\` 当远程主机）；运行时解压同样用系统 tar。
- **运行期**：首启把归档解到 `<userData>/python-runtime/`（sha256 stamp 比对，升级重解），从真实路径拉起解释器。不设 `PYTHONHOME`（python-build-standalone 自定位 prefix，显式设置反而破坏）。
- 备选方案否决记录：PyInstaller（冷启动慢 / 杀软误报 / 无法裁剪）；要求用户装 Python（公开分发不可接受）。

---

## 6. 桌面数据层

- 单库 SQLite：`<userData>/data/agenthub.db` 承载全部 27 张表（engine 单库分支：remote 引擎建全部表 + `get_local_db` 回退 remote factory）。**不连任何远端 PostgreSQL**。
- engine 的 SQLite PRAGMA（WAL / foreign_keys / busy_timeout 30s）在单库引擎上生效。
- 本地固定用户 `local_desktop_user`：首启幂等 seed，本地全部 API 以该用户作用域（`user_id` 隔离字段语义不变）。
- 云端登录账号与本地数据相互独立：不回写、不合并。

---

## 7. 认证（桌面例外 + 云端代理）

- **强制登录**：`<dataDir>/cloud_session.json` 缓存标记（登录/注册/VIP 登录 2xx 写入、登出清除）。无标记 → 登录页；有标记 → 直接进入（离线容忍）。
- **透明代理**：桌面模式 `/api/auth/*` 由 `app/api/auth_proxy.py` 转发到 `AGENTHUB_CLOUD_API_URL`（web 模式挂真实 auth router，行为不变）。Set-Cookie 去 `Domain=` 后透传到本地 origin。
- **本地用户例外**（platform-security delta）：`AGENTHUB_DESKTOP=1` 时 `get_current_user` 无条件解析为固定本地用户，不做逐请求 JWT 验证；本地服务器仅绑定 loopback。web 部署仍执行完整 JWT + 401 语义（测试回归）。
- **离线**：云端不可达 → 代理 503 明确报错（登录页可读 + 重试）；离线登出仍清本地标记；断网且有标记 → 启动进主界面，本地功能全可用。
- **CSRF**：桌面模式放行任意 loopback 端口 origin（前端 origin 是 `127.0.0.1:<ephemeral>`）；非桌面沿用白名单。

---

## 8. 打包（electron-builder）

- `asar: true` + `asarUnpack: [".next/standalone/**"]`：Next standalone 必须是真实磁盘目录（`server.js` 首行 `process.chdir(__dirname)` 跨不进 asar；require 走 `app.asar.unpacked` 路径）。
- `scripts/electron-prebuild.mjs`：拷 `.next/static` + `public` 进 standalone、物化 pnpm 绝对路径 symlink、补齐 tracer 漏拷（allowlist 只保留 Next server 最小集合：next / react / react-dom / @next/env / @swc/helpers）。
- extraResources（只读安装目录）：
  - `resources/python-runtime/python-runtime-<平台>.<zip|tar.gz>` → `python-runtime/`
  - `backend/app`（sidecar 以真实文件系统 import，不进 asar）→ `backend/app`
  - codegraph runtime tar.gz/zip + manifest + notice → `codegraph/`
  - （可选）`cloud-config.json`：`{"cloudApiUrl": "..."}` 构建期写入
- 构建链：`pnpm build`（next build）→ `electron:prebuild` → `electron:tsc` → `codegraph:prepare` → `python-runtime:prepare` → `electron-builder`。
- NSIS：`oneClick: false`、允许改安装目录；DMG：arm64。

---

## 9. 安全模型

- Renderer：`nodeIntegration: false`、`contextIsolation: true`、`sandbox: true`，等同浏览器。
- Preload 白名单（`electron/preload.ts`）只暴露两个能力：`electronAPI.pickDirectory()`（OS 原生目录对话框）与 `electronAPI.getPathForFile(file)`（Electron ≥32 移除 `File.path`，拖拽路径解析唯一入口）。不暴露任何文件系统 / shell 能力。
- 导航防护：站外导航交给 OS 默认浏览器；`file:` 导航一律拒绝（拖拽误导航防护）。
- 不开 `webSecurity: false`、不开 `allowRunningInsecureContent`；LLM 产物 iframe 沙箱与 web 完全一致（`sandbox="allow-scripts"`）。
- API key 存储：与 web 一致（本地 SQLite / user_settings），**不**引入 keychain / safeStorage（macOS ~/Library 与 Windows %APPDATA% 文件权限同级）。
- 绑定路径安全唯一裁决点：`backend/app/utils/workspace_utils.py::is_path_safe`；拖拽与手动填写共用 `POST /api/workspaces/validate-bound-path` 预检 + 创建会话时的完整校验。

---

## 10. 目录路径速查

| 内容 | 打包 | dev |
|---|---|---|
| Next standalone | `<install>/resources/app.asar.unpacked/.next/standalone` | `.next/`（next dev） |
| Python runtime 归档 | `<install>/resources/python-runtime/` | `resources/python-runtime/`（构建产物） |
| Python runtime 解压 | `<userData>/python-runtime/` | 不解压（用 backend/.venv） |
| 数据（DB / memory / workspaces / deployments） | `<userData>/data/` | `.agenthub-data-desktop/` |
| cloud_session.json / jwt-secret | `<userData>/data/` | 同左 |

`<userData>` = `%APPDATA%\AChat`（Windows）/ `~/Library/Application Support/AChat`（macOS）。

---

## 11. 验证清单

**构建 / 冒烟（可自动化）**：
- [x] `pnpm build` 纯 `next build` 通过（无 ABI 前置）
- [x] `pnpm electron:build` 全链产出 NSIS；`release/win-unpacked/` 结构完整（standalone 解包 / python-runtime / backend/app / codegraph）
- [x] 打包形态启动：sidecar 进程拉起、`/health` 200、27 表建齐、jwt-secret 落盘、错误界面（端口占用）呈现
- [x] SSE 经同源 rewrite 实时直通（dev + standalone 双模式，≤45ms 偏差）
- [x] 退出无孤儿进程（sidecar 进程树随 main 退出）

**全新机器验收（人工，Windows 优先）**：
- [ ] NSIS 安装 → 首启「保留并运行」过 SmartScreen
- [ ] 强制登录（cloud-config 指向运营部署）→ 主界面
- [ ] 绑定本地真实目录（原生 picker + 拖拽 + 最近项目三条路径）
- [ ] 发消息（Custom adapter）→ SSE 流式输出实时
- [ ] **bash 审批流**：sandbox / local 双模式 × 审批拦截 / 黑名单 / 路径越界拒绝逐项过（任务 5.5 遗留项在此完成）
- [ ] 重启数据保留（会话 / 绑定 / 配置）；退出无孤儿进程
- [ ] 断网：已登录启动可用；未登录呈现明确错误
- [ ] 安装包体积 < 400MB；主流杀软抽测不误报

---

## 12. 已知残余风险

- 8000 端口被占（错误界面引导兜底）；未签名包被 SmartScreen/Gatekeeper 拦截（引导文档，签名 Phase 2）；杀软对 spawn 误报（签名后消除）；embedded Python 体积（预算内：win 归档 96.8MB）。
