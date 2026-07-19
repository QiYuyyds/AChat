## Context

AChat 是 local-first 的多 Agent 协作平台：前端 Next.js、后端 FastAPI、主库存 PostgreSQL，可选 Milvus/ES/Neo4j/Redis 等。当前已能在服务器上部署完整 Web，用户浏览器访问；Agent 工具与 workspace 语义依赖「执行面与磁盘同机」。

旧桌面方案（`desktop-electron` / change `desktop-electron-python`）假设壳内再起 Next +（或）本机连库/填 infra。与已定产品目标冲突：

- 前端**已经**部署在固定服务器地址
- PG/Milvus 等 infra 在服务器，客户端**不直连数据库**
- 需要 Windows 安装包分发给他人：登录同一账号体系即可用
- 仍需本机目录、CLI、弱网续跑 → 本机必须有引擎进程

本设计定义新权威桌面架构：**Tauri 壳 + 本机引擎 + 远端官方前端/API**。

## Goals / Non-Goals

**Goals:**

- Windows 安装包：双击安装 → 启动壳与本机引擎 → 打开官方前端 → 登录后使用与 Web 同一套 AChat
- Agent 循环、工具、本机 workspace/CLI 在本机引擎执行；模型按现有逻辑直连厂商
- 在线主数据经 HTTPS 写云端 API（PG 权威）；不暴露 DB 连接串给客户端
- 离线用本机 SQLite 续跑，上线尽力同步；多设备冲突 v1 仅提示
- 本机引擎防任意网页调用（engine token + 官方前端 Origin）
- 整包自动更新；v1 不代码签名
- 与现有服务器部署对接，不强制先拆分前端/API/infra 拓扑

**Non-Goals（v1）:**

- macOS / Linux 安装包
- 代码签名 / 微软商店
- 用户可配置第三方服务器地址（安装包写死官方 URL）
- 捆绑 Claude/Codex CLI
- 模型请求统一云端代理
- 多设备离线精致合并 / CRDT
- 在本机再起 Next 或打包第二份前端业务 UI
- 重做 Web/桌面功能产品矩阵（同一产品）

## Decisions

### D1: 交付形态 = 远端前端 + 本机引擎（非纯套壳、非本机全栈 UI）

**Choice**: Tauri 窗口加载固定 `OFFICIAL_WEB_URL`；安装包含壳 + 本机引擎运行时；不打包/不启动本机 Next。

**Rationale**: 前端已在服务器维护一份即可；本机目录/CLI/离线要求必须有本机进程，故不能纯套壳。

**Alternatives**: 纯套壳（无本机能力）；本机再起前端（重复部署、包更大）。

### D2: 壳技术 = Tauri 2

**Choice**: Windows 上用 Tauri 2 做窗口、托盘、单实例、更新、原生目录对话框、注入桥接。

**Rationale**: 业务不在壳内；Tauri 包体与系统 WebView 更合适。架构与 Electron 可替换，但本 change 锁定 Tauri。

**Alternatives**: Electron（更熟、包更大）；Wails 等（生态弱于 Tauri 对本项目）。

### D3: 本机引擎 = 现有后端 desktop 模式，Agent 在本机跑

**Choice**: 同一套 FastAPI/服务代码，`ACHAT_RUNTIME=desktop`；负责 AgentRunner/工具/CLI/workspace；在线持久化走云端 HTTP 客户端；离线走 SQLite。

**Rationale**: 复用 Adapter/工具/沙箱；避免「云端跑 Agent 再遥控本机文件」的复杂桥。

**Alternatives**: 云端跑 Agent + 本机 tool bridge（链路长、离线差）；完全独立第二后端（双份逻辑）。

### D4: 数据平面 = 在线云端 PG 权威 + 本机 SQLite 离线缓存

**Choice**:

| 状态 | 会话/消息/Agent/Key | workspace 文件 | RAG/记忆 |
|---|---|---|---|
| 在线 | 云端 API → PG | 用户本机磁盘 | 云端 API → infra |
| 离线 | 本机 SQLite 队列/缓存 | 用户本机磁盘 | 不可用或只读缓存（若有） |

上线后：本机引擎上传离线产生的变更；冲突不静默覆盖，提示用户。v1 不承诺双机同时离线无感合并。

**Rationale**: 满足「服务器放 PG/Milvus」与「核心可降级」；控制同步复杂度。

**Alternatives**: 仅在线不可用（体验差）；本机永远主存（与云端权威矛盾）。

### D5: 安全 = loopback + engine token + 官方 Origin

**Choice**:

1. 引擎只 bind `127.0.0.1`，端口动态分配，写入壳与 `window.achatDesktop`
2. 启动时生成 `engineToken`（进程级随机），注入前端；本机 API 校验 header（如 `X-Engine-Token`）
3. 校验 `Origin` 为配置的官方前端 origin（可配置列表）
4. 用户 JWT 仍用于云端 API；与 engine token 分离

**Rationale**: https 页面调 http://127.0.0.1 时，必须防其它站点滥用本机引擎；token 不依赖「有没有品牌官网」，官方前端固定 URL 可做 Origin 辅校验。

**Alternatives**: 仅 Origin（可被绕过）；仅 token；命名管道/自定义协议（实现重，v1 不做）。

### D6: 桌面识别 = `window.achatDesktop` 注入

**Choice**: 壳在加载官方前端后注入：

```ts
window.achatDesktop = {
  isDesktop: true,
  engineBaseUrl: string,  // http://127.0.0.1:<port>
  engineToken: string,
  appVersion: string,
  selectDirectory(): Promise<string | null>,
  openPath(path: string): Promise<void>,
  getEngineStatus(): Promise<'starting' | 'ready' | 'error'>,
  restartEngine(): Promise<void>,
}
```

前端以 `isDesktop` 为准启用本机能力；另可 ping 引擎 health 展示状态。

**Alternatives**: URL query `?desktop=1`（易伪造）；纯探测 127.0.0.1（易误判）。

### D7: 模型 Key = 云端存储，本机直连厂商

**Choice**: Key 存在云端 `user_settings`（既有）；本机引擎在用户登录后经云端 API 拉取（传输必须 HTTPS）；Adapter 仍本机直连模型 API，不经云端代理。

**Rationale**: 与现有 `build_adapter_input` / Custom Adapter 路径一致。

**Alternatives**: 云端统一代理（更安全藏 Key，但 v1 成本高）。

### D8: CLI = 检测不捆绑

**Choice**: 引擎启动或首次用 CLI Agent 时检测 `claude`/`codex` 是否在 PATH；缺失则 UI 引导安装；不把 CLI 打进安装包。

### D9: 更新 = 整包，预留分轨

**Choice**: v1 使用 Tauri updater（或等价）整包替换壳+引擎；manifest 与安装包同发版通道。架构上版本号可区分 shell/engine 以便日后分轨。

### D10: 签名 = v1 不做

**Choice**: 接受 SmartScreen；文档说明「仍要运行」。正式对外再签。

### D11: 与旧 spec/change 关系

**Choice**: 本 change 为桌面权威实现。`openspec/specs/desktop-electron` 需求整体切换语义（名称可暂保留以免大范围 rename，内容指向 Tauri 路线）；`openspec/changes/desktop-electron-python` 标记 superseded，不继续实施。

### D12: 进程与启动时序

```
1. 用户启动 AChat.exe（单实例锁）
2. 壳分配/读取 data dir（%APPDATA%/AChat）
3. 生成 engineToken，spawn 本机引擎：
   achat-engine serve --bind 127.0.0.1 --port 0 --data-dir ... --engine-token ...
4. 等待 GET /healthz（含 token）成功或超时错误页
5. 注入 achatDesktop，导航至 OFFICIAL_WEB_URL
6. 用户登录云端；前端用 cookie/JWT 调云端，用 engineToken 调本机
7. 退出：SIGTERM/优雅停引擎 → 刷盘 SQLite → 退出壳
```

### D13: 前端 API 分流（概念）

| 调用 | 目标 |
|---|---|
| 登录/注册/会话列表/设置/云端权威 CRUD/RAG | `OFFICIAL_API_URL` |
| 启动 run、工具、本机 SSE/事件流（desktop） | `engineBaseUrl` |
| 会话消息历史（desktop 读路径） | **优先本机引擎**（见 D16）；失败再回官方 API |
| 选目录 | `window.achatDesktop.selectDirectory` |

具体哪些 REST 仍打云端、哪些打本机，以「Agent 执行在本机、权威持久化在云端」为原则列路由表（tasks 中落地）。原则：

- **写权威业务数据**：云端（在线）或 SQLite 队列（离线）
- **跑 Agent / 工具 / 本机 SSE**：本机引擎
- **读本机刚跑完的消息**：desktop 优先引擎本地 DB（Agent 回复先落引擎；云端 mirror 可能滞后）
- **读云端会话列表 / 多设备权威视图**：官方 API

### D14: SSE 用户鉴权 = 与 REST 同一桌面路径（非本地 JWT_SECRET）

**Choice**: 桌面模式下本机引擎的 `/api/stream` 与受保护 REST 一样，用 `resolve_desktop_user`（官方 access token → 官方 `/api/auth/me` → 本地 shadow User）解析 `user_id`，再订阅进程内 `event_bus`。引擎本地 `JWT_SECRET` 可与官方不同（甚至随机），**不得**作为桌面 SSE 认人的唯一依据。engine token 仍由 middleware 校验「是不是本机壳」。

**Rationale**: Agent 事件发在本机引擎 bus 上；前端 EventSource 必须订同一进程。官方 JWT 由官方 API 签发，引擎若只用本地 secret 验签会 401，出现「POST/Agent 已完成、UI 无流式回复」。**不要**把官方 `JWT_SECRET` 拷进桌面引擎当正修。

**Alternatives**: 强制引擎与官方共用 `JWT_SECRET`（本机可 hack、生产把官方密钥塞进桌面进程不可接受）；仅 cookie 同源（桌面跨 origin + credentials omit 不可行）。

### D15: 桌面 SSE 连接时序 = 等桥注入后再订引擎

**Choice**: `StreamProvider` 在检测到 Tauri 壳 / `window.achatDesktop` 未就绪时，**不得**把 EventSource 永久订到官方 API bus。须等待 `engineBaseUrl` + `engineToken` + access token；桥晚到则重连到本机引擎。

**Rationale**: 壳先 navigate 官方前端、后 eval 注入桥；若登录后立刻按「无桥 = web」连 `:8000`，模块级 singleton 会锁死在错误进程，本地 Agent 事件永远到不了 UI。

**Alternatives**: 仅初始化脚本保证桥先于页面 JS（实现脆弱）；强制用户手动刷新（体验差）。

### D16: 桌面消息读路径 = 引擎优先 + 发送后兜底拉取

**Choice**:

1. `fetchMessages` 在 desktop 优先 `GET {engineBaseUrl}/api/conversations/{id}/messages`，失败/空再回官方 API。
2. 引擎侧 `GET/POST .../messages` 均先 `ensure_conversation_context`（mirror）再 ownership，与本地 SQLite 空库兼容。
3. 发送成功若有 `runIds`，UI 可在短延迟后 best-effort 再拉一次引擎消息（SSE 丢包/迟到时仍能显示完整回复）。

**Rationale**: Agent 回复先写引擎 DB；UI 不靠 POST body 拿正文。仅订官方 bus 或仅读官方历史 → 「库里有回复、屏幕没有」。

### D17: 未认证 UI = 转登录，不白屏

**Choice**: `AuthGate` 在 loading / 未认证受保护路由 / 已认证访问登录页时，渲染带背景的 spinner 并 `router.replace`，**禁止** `return null` 导致 WebView 白板。

**Rationale**: 桌面 WebView 无浏览器默认 chrome，空白页不可诊断。

### D18: 打包

- 壳：Tauri bundler → NSIS/MSI 安装器
- 引擎：嵌入式 Python 或 PyInstaller/Nuitka 产物作为 sidecar，置于资源目录
- 配置：编译期或安装包内 `official.json`：`webUrl`、`apiUrl`、`allowedOrigins`
- 目标体积：中等（约 300–600MB），主要来自 Python 运行时与依赖

### D19: https 前端 → http://127.0.0.1

**Choice**: 本机引擎启用 CORS：允许官方 Origin；浏览器 mixed content 对 loopback 在 Chromium 系通常允许访问 localhost。实现阶段用 WebView2 验证；若受阻则备选：壳做 `plugin` 反向代理把本机引擎挂到自定义协议或壳内 localhost 网关。

**Rationale**: 先走标准 HTTP loopback；备选不阻塞主设计。

### D20: 品牌图标 = 单一源资产 + 派生桌面多尺寸 + 编译嵌入 + 运行时 set_icon

**本轮范围**：以**桌面壳**（窗口 / 任务栏 / 快捷方式 / 安装包）图标为准。Web 站点 favicon 与登录页品牌图**不是本轮验收对象**（源文件可复用，但不要求改 Web UI 或做 Web 专项验收）。

**Choice（与实现一致）**：

| 角色 | 路径 / 代码 | 说明 |
|---|---|---|
| **源资产（权威）** | `src/app/favicon.ico` | 单层 256×256 32bpp ICO（PNG 压缩 payload）；换 logo 只改此文件（或替换后重跑脚本） |
| **派生输出** | `apps/desktop/src-tauri/icons/*` | `icon.png`（256）、`32x32.png`、`128x128.png`、`henry.w@example.net`、多尺寸 `icon.ico`（16/24/32/48/64/128/256）、`icon.icns` |
| **再生脚本** | `apps/desktop/scripts/generate-icons.py` | Pillow 从源 ICO 解 PNG → 写全套 icons；文档见 `apps/desktop/README.md`「Brand icons」 |
| **打包配置** | `apps/desktop/src-tauri/tauri.conf.json` → `bundle.icon` | 列表含 `icons/icon.png`、`icons/icon.ico`、`32x32`、`128x128`、`henry.w@example.net`、`icon.icns` |
| **运行时窗口图标** | `apps/desktop/src-tauri/src/lib.rs` | `include_bytes!("../icons/icon.png")` + `Image::from_bytes` + `window.set_icon(...)`，避免仅依赖旧 exe 资源表 |
| **Cargo feature** | `apps/desktop/src-tauri/Cargo.toml` | `tauri = { features = ["image-png"] }`（否则 `Image::from_bytes` 不可用） |
| **非目标** | `public/agent-icons/*`；完整 brand kit | Agent 头像不是产品 logo；不强制 SVG wordmark / 多主题套装 |

**派生与生效规则（必须按序）**：

1. 源：`src/app/favicon.ico`
2. 运行 `python apps/desktop/scripts/generate-icons.py`，**覆盖** `apps/desktop/src-tauri/icons/`
3. 删除嵌套残留 `apps/desktop/apps/`（若再出现）
4. **必须重新编译壳**：`cd apps/desktop && pnpm dev` 或 `cargo build` / `pnpm build`  
   - **只换 icons 文件、不 rebuild = 窗口/任务栏仍可能是旧图**（图标嵌在 exe / 安装包资源里）
5. 运行时：`setup` 内对 `main` WebviewWindow 再 `set_icon` 一次（双保险）
6. 验收（桌面）：新编 exe / 新安装包的**窗口标题栏图标**；安装版另验开始菜单/快捷方式（可能受 Windows 图标缓存影响）

**Rationale**: 桌面交付必须有正确产品 mark；源复用仓库已有 favicon 避免第二套 logo；`set_icon` + rebuild 是 Windows 上「文件已换但 UI 仍旧」的实际修复路径。

**Alternatives**: 仅靠 `bundle.icon` 不 set_icon（易被旧资源/缓存坑）；手改各尺寸不写脚本（易漂移）；新建独立 `brand/`（v1 多余）。

**实现落点（已实现，方案与代码对齐）**：

| 项 | 落点 |
|---|---|
| 生成脚本 | `apps/desktop/scripts/generate-icons.py` |
| 文档 | `apps/desktop/README.md` § Brand icons |
| 运行时 set_icon | `apps/desktop/src-tauri/src/lib.rs`（`APP_ICON_PNG`） |
| image-png | `Cargo.toml` `tauri` features |
| bundle.icon | `tauri.conf.json`（含 `icon.png` 为首项之一） |

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| WebView2 缺失/过旧导致白屏 | 安装器检测/引导安装 WebView2 Evergreen；启动失败诊断页；AuthGate 不 `return null`（D17） |
| https→http://127.0.0.1 被策略拦截 | D19 备选壳内代理；CI 真机验证 |
| 官方 JWT 与引擎 JWT_SECRET 不一致导致 SSE 401 | D14：`resolve_desktop_user`，不共享官方 secret |
| 桥注入晚于登录，SSE 订到官方 bus | D15：等桥 + 迟到重连 |
| Agent 已落引擎 DB，UI 只读官方历史看不到回复 | D16：读路径引擎优先 + 发送后兜底拉取 |
| 离线同步冲突丢数据 | v1 明确提示；上传前版本/时间戳校验；冲突箱可二期 |
| 本机引擎被恶意页面探测端口 | 动态端口 + 强 token + Origin；不监听 0.0.0.0 |
| Key 进入本机内存 | 仅登录后拉取；内存持有；不做日志打印；后续可加 DPAPI 可选 |
| 包体偏大 / 杀软误报 | 中等包体预期；v1 不签名换速度；文档说明 |
| 与旧 Electron 代码并存混淆 | 新目录 `apps/desktop`；旧 electron 路径标记 deprecated |
| ES/Milvus 等 infra 在桌面引擎日志刷屏 | 可降级，不阻断 Agent；后续可关桌面直连 infra |
| 云端 API 缺少「离线上传/批量同步」 | tasks 列增量 API；无则先消息级 POST 复用现有接口 |
| 官方 URL 写死难以换环境 | 构建 flavor：`official.json` 分 dev/staging/prod，非用户配置项 |
| 桌面仍用 scaffold 占位图标 / 损坏 ico | D20：`generate-icons.py` 覆盖 icons；禁止提交百字节级占位图 |
| 只换 icons 不 rebuild，桌面仍无新 logo | D20 强制 rebuild；`lib.rs` runtime `set_icon`；文档写明流程 |
| 旧安装包/快捷方式 + Windows 图标缓存 | 重装或清缓存；验收以新编 `target/debug|release` exe 窗口图标为准 |
| 误嵌套 `apps/desktop/apps/...` 图标双份 | 删除残留；只保留 `apps/desktop/src-tauri/icons` |
| 缺 `image-png` feature 导致 set_icon 编不过 | `Cargo.toml` 固定 `features = ["image-png"]` |
| 仅换 ico 未换 png 导致糊图 | 脚本一次生成全套尺寸 |

## Migration Plan

1. **文档与 spec**：合并本 change；标注 `desktop-electron-python` superseded
2. **云端**：保持现有部署；按需加同步/Key 下发接口（向后兼容）
3. **前端**：合并 bridge 代码；无注入时行为与现网 Web 一致
4. **本机引擎**：desktop 模式可本地 `pnpm/pytest` 旁路验证
5. **壳**：Windows 安装包内测通道
6. **回滚**：用户卸载桌面端；Web 不受影响。前端 bridge 在无注入时 no-op，可保留

## Open Questions

1. 官方 `webUrl` / `apiUrl` 的正式生产值与是否同域（影响 cookie）
2. 桌面模式下 SSE：本机引擎事件 vs 云端事件是否双通道，如何去重
3. 离线 SQLite schema 范围（全量镜像 vs 仅 outbox + 最近缓存）
4. 引擎打包最终选型（embeddable CPython + venv vs PyInstaller one-folder）
5. 登录 cookie 在 Tauri WebView 加载跨站官方前端时的 SameSite 行为是否要改用 Bearer 镜像（前端已有 cross-origin `authFetch` 路径可复用）
