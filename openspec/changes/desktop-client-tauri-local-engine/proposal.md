## Why

AChat 已具备完整 Web 产品与远端服务器部署（前端 + API + PG/Milvus 等），但缺少可分发给他人的 Windows 桌面安装包。现有 `desktop-electron` / `desktop-electron-python` 方案基于「壳内再起前后端、或 Electron 多进程全量打包」的旧假设，与「前端已部署在服务器、infra 在云端、本机只需跑 Agent/工具」的目标不一致。需要一条新的桌面交付路径：用户下载安装后登录官方账号即可使用同一套 AChat，并在本机执行目录绑定、CLI Agent 与弱网续跑。

## What Changes

- **新增 Tauri 2 Windows 桌面壳**：独立窗口、托盘、单实例、整包自动更新（v1 不代码签名）；窗口加载**已部署的固定官方前端 URL**，不在本机启动 Next.js
- **新增本机引擎（desktop 运行模式）**：安装包内嵌 Python 运行时 + 精简后端可执行/启动器；负责 Agent 循环、工具执行、本机 workspace/CLI、离线 SQLite；**不**直连远端 PG/Milvus
- **云端保持现有部署拓扑**：桌面只对接现有前端 URL 与 API URL；在线主数据（会话/消息/Agent/Key 等）以服务器 PG 为准，经 HTTPS API 访问
- **前端增加桌面桥接**：壳注入 `window.achatDesktop`；前端识别桌面模式后连接本机引擎；本机引擎以 **engine token + 固定前端 Origin** 鉴权，防止任意网页调用 `127.0.0.1`
- **离线续跑**：infra/API 不可用时本机 SQLite 继续对话与跑 Agent；恢复后尽力上传；v1 不做精致多设备离线合并（冲突提示）
- **分发策略**：中等体量安装包（约 300–600MB 量级，含引擎运行时）；CLI（Claude/Codex）不捆绑，仅检测并引导安装；workspace 文件始终在用户本机
- **桌面品牌图标（本轮只验收桌面）**：源资产 `src/app/favicon.ico`；脚本 `apps/desktop/scripts/generate-icons.py` 派生 `apps/desktop/src-tauri/icons/*`；`tauri.conf.json` `bundle.icon` 引用；壳 **rebuild 嵌入** + 启动时 `window.set_icon`（`image-png`）；替换 scaffold 占位图。Web 标签/登录页**不在本轮验收范围**（源可共用，不强制改 Web）
- **明确废弃旧桌面路线**：本变更为权威桌面方案；不继续实现 `desktop-electron-python` 的「本机再起 Next + 用户填 infra 连接串 / 直连库」模型
- **同一产品，非第二套功能清单**：桌面是 AChat 的 Windows 交付形态，不重新划分 Web/桌面功能矩阵；本机能力在桌面自然生效

## Capabilities

### New Capabilities

- `desktop-shell`: Tauri 2 Windows 壳——窗口生命周期、单实例、托盘、打开官方前端、注入 `window.achatDesktop`、启动/守护本机引擎、原生目录选择、整包自动更新入口、产品 brand 窗口图标（bundle + runtime set_icon）
- `desktop-local-engine`: 本机引擎 desktop 模式——进程启停与 health、动态端口、engine token、Agent/工具在本机执行、模型直连厂商、离线 SQLite、与云端 API 的在线读写及上线同步
- `desktop-bridge`: 前端与本机引擎桥接——桌面模式检测、`achatDesktop` 契约、本机 API 调用约定、引擎就绪/失败 UI 状态、与现有 auth/SSE/云端 API 的共存规则
- `desktop-distribution`: Windows 安装与更新——打包内容（壳+引擎运行时）、写死的官方前端/API 地址、首次安装/卸载、整包更新通道、v1 不签名与 SmartScreen 预期、安装包与快捷方式品牌图标与官方 Web favicon 同源

### Modified Capabilities

- `desktop-electron`: **废弃为权威实现路径**。需求改为：桌面交付采用 Tauri + 本机引擎 + 远端官方前端/API；不再要求 Electron 内嵌 Next standalone、不再要求用户配置数据库连接串作为默认路径
- `frontend`: 增加桌面模式识别与本机引擎客户端；在桌面环境下启用本机目录选择等桥接能力；不维护第二套业务 UI
- `user-auth`: 桌面必须登录同一账号体系；Key 随账号同步到服务器，本机引擎在线拉取后用于直连模型；可开关注册/邀请码沿用云端策略
- `platform-security`: 本机引擎仅绑定 loopback；强制 engine token + 官方前端 Origin 校验；desktop 模式禁止默认暴露公网端口；workspace 路径校验仍在本机生效
- `persistence`: 明确在线权威存储为云端 PG；desktop 增加本机 SQLite 离线缓存/队列语义与上线同步约束（v1 冲突策略：提示、不做精致合并）

## Impact

- **新增代码**：`apps/desktop/`（或 `desktop/`）Tauri 工程；本机引擎入口与 desktop 配置（`backend` 内 `desktop` 运行模式 / sidecar 打包脚本）；前端 `window.achatDesktop` 类型与 bridge 模块
- **现有后端**：增加 desktop 模式启动参数（data-dir、bind 127.0.0.1、动态端口、health、engine token 中间件）；在线路径改为调用云端 API 的客户端适配（或复用同一代码 cloud/desktop 双模式）；离线 SQLite 存储层
- **现有前端**：检测桌面注入、本机引擎 base URL/token、选目录走桥接、引擎状态提示；云端登录/SSE 保持
- **现有服务器**：v1 **不要求改部署拓扑**；可能需少量 API（Key 安全下发、离线变更上传、同步状态）——以增量接口为主
- **依赖/工具链**：Rust/Tauri 2 构建链、Windows 安装器（如 NSIS/MSI 经 Tauri bundler）、Python 嵌入/打包工具；不引入用户侧 Node 作为运行依赖
- **包体与体验**：安装包中等体量；双击后壳起引擎 → 打开官方前端 → 登录；无 CLI 时引导安装；断网可有限续跑；**桌面**安装器/快捷方式/窗口图标为产品 mark
- **品牌资产（桌面）**：
  - 源：`src/app/favicon.ico`
  - 脚本：`apps/desktop/scripts/generate-icons.py` → `src-tauri/icons/*`
  - 配置：`tauri.conf.json` `bundle.icon`（含 `icon.png` / `icon.ico` 等）
  - 运行时：`lib.rs` `include_bytes` + `set_icon`；`Cargo.toml` `image-png`
  - 流程：换源 → 跑脚本 → **必须 rebuild 壳** → 用新 exe/安装包验收
  - 清理：删除 `apps/desktop/apps/` 嵌套残留 icons（若存在）
- **文档**：CLAUDE.md / OVERVIEW 中桌面描述改为本方案；旧 Electron 文档标记 superseded；`apps/desktop/README.md` Brand icons 节
- **非目标（v1）**：macOS/Linux 安装包、代码签名、模型请求云端统一代理、多设备离线 CRDT 合并、微软商店、用户可配置第三方服务器地址、安装包捆绑 Claude/Codex CLI、全面重做营销站/多套 brand kit、**本轮 Web favicon/登录页专项改造与验收**
