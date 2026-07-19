## Context

AChat 是多 Agent 协作平台：前端 Next.js、后端 FastAPI、主库存 PostgreSQL，可选 Milvus/ES/Neo4j/Redis 等。Web 形态前后端可分部署；桌面形态需要 Windows 安装包。

### 历史 v0（已实现骨架，产品方向已 pivot）

早期决策：**Tauri 壳 + 本机引擎 + 远端官方前端/API**。引擎不直连 PG/infra，在线权威经官方 HTTPS API；前端双平面（云权威 + 本机执行）。对应实现与 tasks 1–12 已完成勾选。

### 当前 v1 产品方向（2026-07-19 用户对齐）

```
用户电脑（安装包）
├─ Tauri 壳
├─ 本地静态前端   ← 只访问本机后端；不访问官方业务站/业务 API
└─ 本地后端引擎   ← 完整 FastAPI 业务进程
        │
        ├─ 直连默认官方基础设施（PG / Milvus / ES / Neo4j …）
        ├─ 设置页可改为用户自己的 infra
        ├─ 模型：用户 API Key 直连厂商 + 本机 Claude/Codex CLI
        └─ 本机 SQLite 缓存 + 服务器 PG 主库
```

**不依赖**：官方 AChat 业务 Web / 官方业务 API 中转。  
**仍依赖（默认）**：服务器上的基础设施。  
**可选**：用户自配 infra。

## Goals / Non-Goals

**Goals:**

- Windows 安装包：安装 → 启动壳与本机引擎 → 打开**本机静态前端** → 登录（同一多用户体系，本机后端对主库鉴权）→ 使用与 Web 同构的产品能力
- 前端业务请求只打本机引擎；无桥时纯 Web 行为不变
- 本机引擎完整提供 REST/SSE；Agent/工具/workspace 在本机
- 默认直连官方 infra；设置可覆盖为用户服务器
- 主库 PG 权威 + 本机 SQLite 缓存/outbox；弱网续跑；冲突可见
- 模型：API Key 本机直连 + CLI 检测不捆绑
- loopback + engine token + 本机 Origin；沙箱/黑名单
- 整包更新；v1 可不签名

**Non-Goals（v1）:**

- macOS / Linux 安装包
- 代码签名 / 微软商店
- 捆绑 Claude/Codex CLI
- 模型统一云端代理
- 多设备离线 CRDT
- 把 infra 进程（PG/Milvus…）打进安装包
- 继续以**远程官方前端**作为桌面主 UI（v0 路径退役）
- 强制用户只能用官方 infra、不可自配（已明确要可自配）

## Decisions

### 状态说明

| 编号 | 状态 |
|---|---|
| D1–D13、D18 部分、D19 中「官方远程前端」假设 | **Superseded** by D21+（实现可残留，须按 D21+ 改造） |
| D14–D17、D20 | **仍有效**（SSE 本机、读路径、AuthGate、图标），语义上「官方」改为「本机引擎/主库」处见修订注 |
| D21–D31 | **当前权威** |

---

### D21: 交付形态 = 本机静态前端 + 本机完整后端 + 远程 infra（权威）

**Choice**: 安装包内嵌静态前端资源 + local engine（完整 FastAPI）。壳在引擎 health 就绪后导航到**本机** UI origin（通常 `http://127.0.0.1:<enginePort>/`）。**不**导航远程 `OFFICIAL_WEB_URL` 作为主路径。

**Rationale**: 用户要求前后端不访问官方业务服务器；infra 仍可在服务器。静态内嵌（方案 A）避免本机再起 Node/Next 长驻。

**Alternatives**: v0 远程前端（已否决）；本机再起 Next standalone（更重，方案 B，非本轮）。

**Supersedes**: D1, D12 步骤 5–6 的远程导航假设, D13 双平面云路由表。

### D22: 壳技术 = Tauri 2（不变）

同 D2。业务仍不在壳内；壳只做窗口/生命周期/桥/更新。

### D23: 本机引擎 = 完整业务后端 + 直连 infra

**Choice**:

- 同一套 FastAPI 应用在 `ACHAT_RUNTIME=desktop` 下启动
- 提供登录/注册、会话、消息、设置、Agent、stream 等完整 API（与 Web 后端同构）
- **直连** `DATABASE_URL` 及 Milvus/ES/Neo4j 等（经 `infra/factory` 既有降级语义）
- 默认连接串来自打包配置；用户设置可覆盖并持久化到本机（加密/权限按实现选定，不得打日志）
- **不再**要求在线路径必须经官方业务 HTTPS API client（v0 CloudApiClient 主路径退役为可选兼容或删除）

**Rationale**: 「不访问业务服务器」+「infra 还在服务器」= 客户端进程直连 infra。

**Supersedes**: D3 中「在线持久化走云端 HTTP 客户端」；D4 表中「仅经云端 API 访 infra」；local-engine spec 旧「MUST NOT open direct database connections」。

### D24: 数据平面 = 远端 PG 主库 + 本机 SQLite 缓存

**Choice**:

| 状态 | 账号/会话/消息/设置 | workspace 文件 | RAG/记忆 |
|---|---|---|---|
| 在线 | 本机引擎 **直连** PG 读写 | 用户本机磁盘 | 本机引擎直连 infra（可降级） |
| 弱网/离线 | 本机 SQLite 缓存/outbox 续跑 | 用户本机磁盘 | 不可用或只读缓存 |
| 恢复 | outbox 尽力同步回主库；冲突提示，不静默覆盖 | — | — |

**Rationale**: 用户选择数据模型 C。

**Note**: v0 outbox「上传到官方业务 API」改为「同步到主库 PG / 本机引擎既有写入路径」。

### D25: 前端 API 一律本机（desktop 模式）

**Choice**:

| 调用 | 目标 |
|---|---|
| 一切业务 REST/SSE（auth、会话、设置、Agent、stream…） | `engineBaseUrl`（或同源本机托管） |
| 选目录 / 引擎状态 / 重启 | `window.achatDesktop` |
| 纯 Web（无桥） | 现有 `API_BASE_URL` 行为不变 |

**Rationale**: 用户「只改前端访问本地」在完整语义下 = 桌面 WebView 内无官方业务 API 依赖。

**Supersedes**: D13 分流表；`DESKTOP_OFFICIAL_CLOUD_PATH_PREFIXES` 默认桌面路径。

### D26: 静态前端构建与托管

**Choice**:

1. 增加 desktop 前端构建产物（静态 export 或「构建后由引擎挂载的资产目录」；实现阶段选定可离线路径，处理 `next/font/google` 等）
2. 资源进入安装包，例如 `resources/ui/**` 或引擎可服务目录
3. 引擎在 desktop 模式挂载静态文件与 SPA fallback（`/` → `index.html`）
4. 壳 `navigate` 到 `http://127.0.0.1:{port}/`（与 API 同 origin 可简化 cookie；若 token 已走 Bearer，同源仍更简单）

**Rationale**: 方案 A 安装包内嵌；与引擎同端口可避免 CORS/mixed 复杂度。

### D27: 配置模型 = 默认官方 infra + 用户可覆盖

**Choice**:

- 打包内嵌 `infra.default.json`（或扩展现 `official.json`）：
  - `databaseUrl` / 各 infra 端点与必要密钥引用方式
  - `allowedOrigins` 本机相关
  - **不再**要求远程 `webUrl` 作为运行时主入口
- 首次启动用默认配置
- 设置页：「使用自定义服务器」→ 用户填写/导入 → 写入 `%APPDATA%/AChat/config`（覆盖默认）
- 未填自定义时始终用打包默认

**Rationale**: 用户选择配置模型 C。

**Security**: 默认连接串视为敏感；文档说明泄露面；用户自定义凭据禁止 log；生产应用最小权限 DB 账号。

### D28: 登录与 Key

**Choice**:

- 多用户注册/登录 API 由本机引擎提供，校验与存储落在主库 PG（与 Web 同一 schema/策略）
- JWT 由本机引擎签发/校验（使用本机配置的 `JWT_SECRET` 或与部署约定一致的 secret——实现阶段：桌面直连同一 PG 时需与 Web 部署协调 token 互通策略；若桌面与 Web 需同一 token 互认，则共享 secret 或统一 issuer；**默认**：桌面独立会话即可，不要求与浏览器 cookie 互通）
- 用户 API Key 存主库 `user_settings`；本机引擎本地读库即可，无需「云 handoff 拉 key」
- CLI Agent：PATH 检测，不捆绑

**Rationale**: 用户选择登录 A、模型 C。

**Supersedes**: D7「仅从云端 API 拉 key」；D14 中「必须 resolve 官方 /api/auth/me」在纯本地权威下可简化为本地 JWT；若仍保留兼容桥可双路径。

### D29: 安全 = loopback + engine token + 本机 Origin

**Choice**:

1. 引擎 bind `127.0.0.1`，端口动态
2. 进程级 `engineToken`，前端经桥注入；受保护本机 API 校验
3. Origin allowlist 包含本机 UI origin（如 `http://127.0.0.1:<port>`）；不再依赖远程官方前端 origin 作为唯一合法来源
4. 用户 JWT 与 engine token 分离（用户身份 vs 壳会话）

**Rationale**: UI 与 API 均在本机后，Origin 模型从「官方 https 站」改为「本机托管页」。

**Note**: 浏览器/WebView 的 Origin 比较是**主机名字符串**，`localhost` ≠ `127.0.0.1`。开发态若 UI 与引擎使用不同 loopback 主机名，仍属跨源；见 **D31**。

### D30: 启动时序（v1）

```
1. 用户启动 AChat.exe（单实例）
2. 壳准备 data dir（%APPDATA%/AChat）
3. 生成 engineToken，spawn 引擎：
   achat-engine serve --bind 127.0.0.1 --port 0 --data-dir ... --engine-token ...
   [--infra-config ...]
4. 等待 GET /healthz 成功
5. 注入 window.achatDesktop（engineBaseUrl/token/...）
6. 导航至 http://127.0.0.1:<port>/  （静态前端）
7. 用户在本机 UI 登录 → 本机 API → 主库 PG
8. Agent 本机执行；infra 按配置直连
9. 退出：停引擎 → 刷盘 → 退壳
```

**Supersedes**: D12 步骤 5–6 远程 URL。

**Dev note**: `tauri dev` 可继续打开 Next `http://localhost:3000`（或 config `webUrl`），引擎仍在 `http://127.0.0.1:<port>`。此为跨源常态，不得依赖「碰巧同源」才能鉴权；必须满足 **D31**。

### D31: Loopback 主机名等价 + 禁止半对齐（权威）

**Context / incident**:

```
网络层:  localhost 通常解析到 127.0.0.1（或 ::1）
浏览器:  Origin = scheme + host + port
         http://localhost:3000  ≠  http://127.0.0.1:12066

dev 典型形态:
  UI  → http://localhost:3000
  API → http://127.0.0.1:<enginePort>     ← 跨源

半对齐 bug（已观测）:
  getApiBaseUrl / URL 对齐 → http://localhost:<port>
  isExecutionUrl / 挂 token 仍按 bridge 的 127.0.0.1 字符串判断
  → 业务 REST 漏 X-Engine-Token → 401
  → SSE 因 ?token=&engineToken= 仍可 200
  → 用户体感为「SSE 没连上 / 桌面半残」
```

**Choice**:

1. **事实写入契约**：浏览器/WebView 不把 `localhost` 与 `127.0.0.1` 当同一 origin；不得假设「同一台机器 = 同源」。
2. **前端 MUST 提供统一 loopback helper**（如 `alignLoopbackHost` / `sameLoopbackService` / `urlTargetsEngine`），`getApiBaseUrl`、`executionBaseUrl`、`isExecutionUrl`、`engineUrl`、StreamProvider base 共用同一套逻辑。
3. **桌面业务 REST MUST 对本机引擎请求始终附带 `X-Engine-Token`**（从 `window.achatDesktop` 读取）。判断「是否引擎目标」时，`localhost` 与 `127.0.0.1`（及 `::1`）在**相同端口**上视为同一 loopback 服务。
4. **SSE**：EventSource 无法设自定义 header 时，继续允许 `?token=` + `?engineToken=`；fetch-sse 兜底可走 header。用户 JWT 与 engine token 仍分离。
5. **禁止半对齐**：不得只在某一层把 host 改写为页面 host，却在 token 挂载 / URL 匹配层仍用未对齐字符串做 `startsWith`。
6. **禁止伪根治**：仅靠「全改成 127.0.0.1」或「全改成 localhost」不作为唯一根治；Windows 上 `localhost` 还可能解析到 `::1`，字符串统一易漏。
7. **prod 优先同源**：发布态仍优先 D26/D30——静态 UI 与 API 同引擎 origin（`http://127.0.0.1:<port>/`），降低 CORS/cookie 复杂度；dev 跨源是额外必须覆盖的路径。
8. **回归门槛**：`page.hostname=localhost` + `bridge.engineBaseUrl=http://127.0.0.1:<port>` 时，`/api/agents`、`/api/conversations` 等业务 REST **不得**因漏 engine token 而 401；SSE 与 REST 不得因 host 别名出现「一边 200 一边 401」的分裂。

**Rationale**: engine token 是壳会话门闩，与用户 JWT 无关；跨源是 dev 常态；半对齐会导致「登录/me/SSE 看起来好了、会话列表全挂」的假性 SSE 故障，必须在设计层封死。

**Implementation pointers**（非绑定路径，供 tasks 对齐）:

- `src/shared/desktop/url.ts` — loopback 等价
- `src/lib/desktop.ts` — `executionBaseUrl` / `isExecutionUrl` / `attachEngineTokenHeaders`
- `src/lib/api.ts` `authFetch`、`src/stores/auth-store.ts`、`src/components/stream-provider.tsx`

---

### 仍有效的既有决策（摘要 + 修订注）

#### D14–D17（SSE / 读路径 / AuthGate）

- **D14 SSE**：事件仍在本机引擎 bus；SSE 订本机 `/api/stream`。修订：身份以**本机 JWT / 本机会话**为主；不再依赖「官方 access token + /api/auth/me」作为唯一路径（可保留为兼容）。跨 loopback 主机名时的 token 挂载见 **D31**。
- **D15 等桥**：仍必须等 `achatDesktop` 再订 SSE，避免订错基址。
- **D16 消息读**：desktop 读本机引擎即可（已是权威执行+本地 API）；「回落官方 API」改为可选/删除。
- **D17 AuthGate**：禁止白屏，仍有效。

#### D18 打包（修订）

- 壳 + **引擎 sidecar** + **静态前端资源** + **默认 infra 配置**
- 引擎：PyInstaller one-folder（v0 已选）
- 体积预期上升（前端资产 + 引擎）

#### D19 mixed content（修订）

- 主路径同源 `http://127.0.0.1` 静态+API，**不再**依赖 https 远程页调 http loopback
- 备选壳内代理仅在特殊拆分部署时需要
- **dev** 允许 `localhost:3000` UI → `127.0.0.1` 引擎（跨源 + D31）

#### D20 品牌图标

- 完全保留：源 favicon → generate-icons.py → bundle + runtime set_icon + 必须 rebuild

#### D8 CLI / D9 更新 / D10 签名 / D11 旧 change

- 保留；D11：本 change 仍是桌面权威；`desktop-electron-python` 仍 superseded

---

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| 默认 DB/infra 连接串打进安装包泄露 | 最小权限账号；TLS；定期轮换；文档威胁模型；敏感项可后期改远程下发引导 |
| 用户网络访问不到官方 infra | 设置改自有服务器；清晰错误；降级提示 |
| Next 静态导出与 App Router 不兼容点 | 实现阶段 spike；不行则引擎侧轻量托管 standalone 静态摘出或内嵌最小静态壳+已构建客户端 |
| C 盘空间不足导致打包失败 | `CARGO_TARGET_DIR`/构建目录放 D 盘；清理 debug target |
| 直连 PG 与 Web 多实例 schema 迁移 | 桌面与服务器共用 schema 版本纪律；启动检查 alembic/revision |
| 旧 v0 代码路径（CloudApiClient、双平面路由）残留 | tasks 明确删除或 feature-flag；避免双写混乱 |
| `localhost` vs `127.0.0.1` 半对齐导致业务 401、假性 SSE 故障 | **D31**：统一 loopback helper；引擎 URL 判断与 `X-Engine-Token` 挂载共用；回归测 page=localhost + bridge=127.0.0.1 |
| JWT 与 Web 不互通 | v1 接受桌面独立登录会话；文档说明 |
| 静态资源与 API 不同 port 的 CORS | 优先同端口托管 |
| 图标/壳已完成但产品方向变 | 不重做图标；只改加载与数据面 |

## Migration Plan

1. **文档**：本 proposal/design/specs/tasks 以 D21+ 为准；标注 v0 已实现部分与待改造部分  
2. **代码**：在现有 `apps/desktop` + `backend/app/desktop` 上改造，不新建平行 change 目录  
3. **前端**：desktop 构建流水线 + API 基址本机化  
4. **引擎**：直连 infra 配置加载；静态托管；弱化/移除强制 official API client  
5. **壳**：navigate 本机 URL  
6. **验证**：本机登录 → 列会话 → 发消息 → 绑目录 → 断网缓存 → 恢复同步  
7. **回滚**：用户卸载桌面；Web 部署不受影响  

## Open Questions

1. Next 静态导出的最终技术路径（`output: 'export'` vs 构建产物摘取 vs 其它）与 `next/font` 离线处理  
2. 默认 infra 连接信息由谁维护、如何在不进 git 明文的前提下注入 CI/打包（secrets 管线）  
3. 桌面 JWT 是否必须与 Web 互认（当前倾向否）  
4. 用户自定义 infra 的配置 schema 与校验 UX 细节  
5. 主库不可达时，注册/登录是否允许纯本地临时模式（v1 倾向：否，仅已登录会话的缓存续跑）  
