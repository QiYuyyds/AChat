## Why

AChat 的代码 Agent 目前依赖 Grep/Read 逐文件理解项目，面对调用链、跨文件依赖和影响范围时工具轮次多。CodeGraph 能提供本地 AST 索引、符号关系、调用路径和影响分析，适合作为独立的 Workspace 源码智能能力。

## What Changes

- 新增仅支持 local Workspace、默认关闭的“源码智能”。
- AChat 托管固定版本 CodeGraph；用户无需安装 Node、CLI 或配置 PATH。
- 创建会话选择“绑定本地目录”时，在路径警告下方显示默认关闭的“启用源码智能”开关。
- 用户确认后后台准备运行时并建立 `.codegraph` 索引，不阻塞创建会话和聊天。
- 聊天顶部 Workspace 工具栏在文件夹图标附近增加源码图谱状态图标。
- 点击图标打开状态面板；面板首行固定为“源码智能”滑动开关，负责启用/停用，下方展示构建进度、统计、错误和操作。
- OFF→ON 时确认下载/使用托管运行时及写入 `.codegraph`；ON→OFF 时确认停止能力，默认保留索引缓存。
- 前端通过独立 REST API 轮询状态，不修改 StreamEvent/SSE。
- 新增统一内置工具 `code_explore(query)`：Custom 直接走 ToolRegistry；Claude/Codex 走现有 AChat MCP Bridge；CodeGraph 本身不使用 MCP。
- 功能关闭或 CodeGraph 失败时，不影响聊天、RAG、Memory、Artifact、SSE、sandbox 和其他 Workspace。

## Capabilities

### New Capabilities
- `code-intelligence`: 托管运行时、Workspace 状态、后台索引、查询与故障隔离。

### Modified Capabilities
- `tools`: 条件暴露 `code_explore`。
- `adapters`: 三类 Agent 共用同一 ToolDef。
- `frontend`: 创建页开关、顶部图标、滑动开关面板和 REST 轮询。
- `platform-security`: 固定版本、SHA256、安全解压、参数化启动和进程清理。

## Impact

- 新增 runtime/index manager、Workspace 内部元数据、REST API、`code_explore`、前端状态面板和打包配置。
- 不修改 Workspace/Conversation DB schema，不改 RAG/Memory/Neo4j，不新增 StreamEvent。
- 只在用户明确启用后向绑定项目写入 `.codegraph`；停用默认不删除。
