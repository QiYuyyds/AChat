## Why

AChat 需要可分发的 Windows 桌面安装包。用户双击安装后应在**本机**跑完整产品 UI 与业务后端（Agent、工具、workspace、登录 API），**不要**再打开/调用官方 AChat 业务站点与业务 API 中转层。

服务器上已有的 **基础设施**（PostgreSQL / Milvus / Elasticsearch / Neo4j 等）仍然可以依赖：桌面后端**直连**这些服务（默认内置官方 infra 连接配置，并允许用户在设置中改成自己的服务器）。业务数据以服务器 PostgreSQL 为主库，本机 SQLite 做缓存与弱网续跑。

本 change 早期（已落地的 v0 实现）曾按「Tauri 壳加载**远程官方前端** + 本机引擎仅作执行平面 + 经官方 HTTPS API 访问云」设计。产品方向已 pivot（2026-07-19 对齐），本文件与 design/specs/tasks 以**新方向为准**；已完成的壳/引擎/桥/图标/离线骨架代码**尽量复用**，但需按新决策改造。

## What Changes

### 产品架构（权威，v1 目标）

- **Tauri 2 Windows 壳**：窗口、单实例、托盘、引擎生命周期、原生目录选择、整包更新入口、品牌图标；**不再**将主 UI 导航到远程 `OFFICIAL_WEB_URL`
- **安装包内嵌本地静态前端**：构建产物打进包（方案 A：静态资源，非再起本机 Next 长驻 dev server）；由本机引擎（或壳约定的本机 origin）托管；前端**只访问本机后端**
- **本机完整后端（local engine）**：同一套 FastAPI 业务进程在本机运行；负责登录/注册鉴权、会话/消息、Agent 循环、工具、workspace、设置读写 API 等；**不经**官方 AChat 业务 HTTP API 中转
- **基础设施默认官方服务器、可自配**：打包内置默认 PG/Milvus/ES/Neo4j 等连接配置；设置页提供可选「使用自己的服务器」覆盖；**不**把「禁止用户配置第三方服务器」作为 v1 非目标
- **数据平面**：服务器 PostgreSQL 为主库（账号/会话等权威）；本机 SQLite 为缓存 + 离线 outbox；上线尽力同步；冲突不静默覆盖
- **模型**：用户 API Key 本机直连厂商 + 本机 Claude/Codex CLI（检测不捆绑），与 Web 能力对齐
- **安全**：引擎默认仅 bind `127.0.0.1`；engine token + 本机前端 Origin（本机引擎 origin / 静态托管 origin）校验；workspace 沙箱与 Windows 命令黑名单仍生效
- **分发**：NSIS 等安装包含壳 + 引擎运行时 + 前端静态资源 + 默认 infra 配置；CLI 不捆绑；v1 可不代码签名（SmartScreen 文档说明）

### 相对 v0（远程官方前端）的明确废弃点

| v0 决策 | v1 决策 |
|---|---|
| 窗口加载远程 `webUrl` | 窗口加载本机引擎托管的静态前端 |
| 前端双平面：auth/会话走官方 API，执行走引擎 | 前端业务 API **一律**打本机引擎 |
| 引擎在线持久化只经官方 HTTPS API，禁止直连 PG/infra | 引擎**直连**远端 PG/infra（默认官方，可自配） |
| 官方业务 API 为账号与主数据唯一入口 | 本机后端直连主库做鉴权与 CRUD；官方业务站/API **不是**桌面运行时依赖 |
| `official.json` 的 web/api 为远程产品入口 | 改为本机相关配置 + **infra 默认连接**（及可选覆盖）；远程业务 URL 不再作为桌面主路径 |

### 保留并复用

- Tauri 工程、`window.achatDesktop` 桥、选目录、引擎 spawn/health/shutdown
- 本机 AgentRunner / adapters / 工具 / 沙箱 / CLI 检测
- 本机 SQLite 离线缓存与 outbox 骨架（语义改为「对主库同步」而非「对官方业务 API 上传」）
- 品牌图标流水线（D20）
- PyInstaller one-folder 引擎打包路径

### Capabilities

#### New / 重定义

- `desktop-shell`: Tauri 壳——生命周期、单实例、注入桥、拉起引擎后打开**本机** UI origin、原生目录选择、更新、品牌图标
- `desktop-local-engine`: 本机完整业务后端——loopback HTTP、engine token、静态前端托管、直连 infra、Agent/工具本机执行、SQLite 缓存与主库同步
- `desktop-bridge`: 前端识别桌面、本机 API 基址 = 引擎、桥能力、引擎状态 UI
- `desktop-distribution`: 安装包内容（壳+引擎+静态前端+默认 infra 配置）、更新、未签名说明
- `desktop-infra-config`: 默认内置官方 infra 连接；设置页可覆盖为用户自有服务器（校验与存储安全约束）

#### Modified

- `desktop-electron`: 仍废弃 Electron 为权威路径；语义进一步改为「本地前后端 + 直连 infra」，而非「远程官方前端」
- `frontend`: 桌面模式全部业务请求打本机；静态导出/嵌入构建路径；无桥时纯 Web 行为不变
- `user-auth`: 同一套多用户注册登录模型；鉴权由本机后端对**主库 PG** 执行（非远程业务 API 中转）
- `platform-security`: loopback + engine token + **本机** Origin allowlist；沙箱/黑名单不变
- `persistence`: 主库 = 远端 PG（直连）；本机 SQLite = 缓存/outbox；同步与冲突策略

## Impact

- **壳**：`inject_and_navigate` 改为打开本机引擎 URL（或本机托管前端），删除「必须远程 webUrl」假设
- **引擎**：从「云 API 客户端附属」升级为「完整本地 API 服务 + 直连 infra」；CLI 参数从强制 `--official-api-url` 转为 infra/config 路径；可托管静态文件
- **前端**：desktop 下 `API_BASE_URL`/`authFetch` 指向本机；双平面官方云路由表退役为可选/删除；增加 desktop 静态构建流水线
- **配置**：`official.json` / 等价配置扩展为 infra 默认值 + 本机 allowedOrigins；设置 API 支持用户覆盖
- **服务器**：官方 **业务前端/业务 API 进程** 对桌面运行时非必需；**infra** 仍为默认依赖（运维侧网络与账号权限需允许桌面客户端直连，或后续加 VPN/隧道——实现阶段评估）
- **安全注意**：直连 PG 意味着连接串进入桌面配置面；默认串打包需评估泄露面；用户自配串不得写入日志；优先 TLS/最小权限账号
- **非目标（v1）**：macOS/Linux 安装包、代码签名、微软商店、捆绑 Claude/Codex CLI、多设备 CRDT、模型统一云代理、把整套 Milvus/ES 进程打进安装包、继续以远程官方前端为桌面主 UI

## User decisions log (2026-07-19)

1. 前端：安装包内嵌静态资源（A），只访问本机  
2. 业务前后端：本机进程，不访问官方 AChat 业务服务器  
3. 基础设施：默认官方服务器（A）+ 可选用户自配  
4. 业务数据：服务器主库 + 本机缓存（C）  
5. 登录：现有多用户，账号在服务器 PG，本机后端直连鉴权（A）  
6. infra 配置：打包默认 + 设置可改（C）  
7. 模型：API Key + CLI 都要（C）  
