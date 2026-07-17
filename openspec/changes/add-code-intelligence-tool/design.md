## Context

AChat-managed tools 由 ToolRegistry 注册。Custom Agent 直接调用 ToolRegistry；Claude Code/Codex CLI 通过现有 `achat-tools` MCP Bridge 调用 allowlist 工具。CodeGraph 是独立本地运行时，使用 tree-sitter 和 SQLite/FTS5 构建 `.codegraph` 索引，与 AChat 文档 RAG/Neo4j 图谱无关。

## Goals / Non-Goals

**Goals:**
- 用户无需自行安装 CodeGraph、Node 或配置 PATH。
- local Workspace 可显式启用，索引后台构建，进度可见、可取消。
- 顶部面板使用滑动开关统一控制启用/停用。
- 三类 Agent 共用一个 `code_explore` handler。
- 默认关闭；失败、关闭、构建中均不影响其他功能。

**Non-Goals:**
- 不接 CodeGraph MCP，不嵌入 npm SDK，不复制索引引擎。
- 不支持 sandbox，不绕过 sandbox 配额。
- 不修改 DB schema、StreamEvent、RAG、Memory 或 Neo4j。
- 停用不默认删除 `.codegraph`。

## Decisions

### D1 — AChat 托管固定版本运行时

仓库保存 runtime manifest：版本、平台/架构、固定 HTTPS URL、SHA256 和许可证。运行时优先使用桌面包 extraResources，其次使用 `.agenthub-data/runtimes/codegraph/<version>/<platform>/` 已校验缓存，否则在用户启用后按需下载。

下载到临时目录，SHA256 通过后安全解压并原子安装。拒绝 latest 浮动版本、摘要不匹配和 archive path traversal；保留 MIT License/NOTICE。

### D2 — local Workspace 显式启用和后台索引

创建会话选择“绑定本地目录”时，在路径选择和真实文件警告下方显示默认 OFF 的“启用源码智能”。sandbox 不显示。创建请求携带 `codeIntelligenceEnabled`；API 创建会话后立即返回，后台依次准备运行时、排队并执行 `codegraph init`。

状态：`disabled / preparing_runtime / queued / indexing / ready / syncing / rebuilding / cancelling / failed / interrupted`。同一项目只允许一个任务；全局索引并发默认 1。

### D3 — 内部元数据而非 DB

状态原子写入 local conversation 的内部 `workspace.root_path`，例如 `.agenthub/code-intelligence.json`，包含 enabled、runtimeVersion、status、phase、counts、timestamps 和有界 error。真实绑定目录只增加 `.codegraph`。重启发现无活任务的非终态记录时标记 `interrupted`。

### D4 — 顶部图标和滑动开关面板

聊天顶部 Workspace 工具栏在文件夹图标附近增加源码图谱图标：灰色未启用、spinner/百分比构建中、绿色 ready、橙色需同步、红色失败。

点击图标打开 popover/侧面板。第一行固定为标题“源码智能”和滑动开关：

- `disabled`：OFF。切到 ON 先确认运行时准备和真实项目 `.codegraph` 写入；确认后调用 enable。
- `preparing_runtime/queued/indexing/syncing/rebuilding`：保持 ON，下方显示进度和取消。
- `ready`：保持 ON，下方显示统计、立即同步和重建。
- `failed/interrupted`：保持 ON，表示启用意图仍存在，下方显示错误和重试。
- enable/disable 请求处理中：开关临时 disabled，避免重复切换。
- ON→OFF：确认停止后台工作、取消 Agent 工具访问、默认保留 `.codegraph`；确认后 cancel+disable，取消确认或请求失败则恢复 ON。

创建页开关和顶部面板开关使用同一 Workspace enable/disable 语义。构建完成或失败只显示一次 toast，不向聊天流插入消息。

### D5 — 独立 REST 轮询

状态和操作使用认证 REST API：enable/status/cancel/sync/rebuild/retry/disable。面板打开或任务非终态时短间隔轮询；ready/disabled 时停止高频轮询并在窗口聚焦时刷新；切换会话或卸载组件必须清理 timer。不新增 SSE 事件。

### D6 — Workspace 能力与统一工具

`code_explore` 只接收 `query`，项目路径来自当前 Workspace。Custom Agent 仅在 local Workspace enabled+ready 时由 AgentRunner 自动注入，不修改 Agent row 或预设。Claude/Codex 通过现有 AChat MCP Bridge 调用同一 ToolDef；prompt 仅在 ready 时引导使用。handler 始终做最终状态校验。

### D7 — 同步、取消和安全

索引 ready 后合并文件变化并去抖运行 `codegraph sync`；用户可立即同步或重建。下载、init、sync、rebuild、explore 都有取消和超时；退出、取消或 Bridge teardown 必须终止完整进程树。

所有 executable 来自 packaged/verified runtime，cwd/project path 来自 Workspace，query 只作为 argv 数据参数。不使用 `shell=True`，不接受模型提供 executable/cwd/env/path。Windows/POSIX 都必须覆盖引号、换行、Unicode 和 `& | < > ^ % !` 注入测试。

### D8 — 零影响隔离

Code intelligence 是独立 L3 service，不是应用启动依赖。必须满足：

- disabled 时不下载、不启动、不轮询、不注入工具；
- CodeGraph 崩溃或下载/索引失败时聊天仍正常；
- 一个 Workspace 索引时其他会话仍正常运行；
- sandbox 不产生 CodeGraph 文件或任务；
- 同项目不并发索引；
- 应用退出后无遗留 CodeGraph 进程。

## Data Flow

```text
创建 local 会话 / 顶部滑动开关 ON
  → CodeIntelligenceManager
  → resolve/download/verify runtime
  → background codegraph init
  → metadata ready

顶部图标/面板 → REST polling → manager/metadata

Custom → ToolRegistry ─┐
                       ├→ code_explore → freshness check → CodeGraph CLI
Claude/Codex → Bridge ─┘
```

## Testing Strategy

- runtime manifest、SHA256、安全解压、原子安装、License。
- local/sandbox、默认关闭、状态机、重启 interrupted、任务互斥和全局并发。
- 创建页开关、顶部图标、面板滑动开关确认/回滚/pending、进度和轮询清理。
- REST 权限和状态转换；确认无 StreamEvent 新增。
- Custom 条件注入、CLI Bridge、降级、输出边界。
- 取消、超时、跨平台 argv、进程树和 feature-off/多会话回归。
