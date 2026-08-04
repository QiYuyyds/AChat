# AChat 项目全貌（OVERVIEW）

> **这份文档是给 AI / 新对话窗口的「全貌速览」**：读完它，不翻代码也能掌握项目做了什么、怎么分层、代码在哪、当前进度。
>
> 与其它文档的分工：`OVERVIEW.md` 给**地图**（做了什么 / 代码在哪）· [CLAUDE.md](./CLAUDE.md) 定**规则**（怎么做 / 不做什么）· [ARCHITECTURE.md](./ARCHITECTURE.md) 定**架构**（五层 + 数据流 + 基础设施）· `specs/` 定**规格**（每个模块的字段与契约）· `skills/` 给**配方**（扩展任务步骤化指南）· `README.md` 面向**人类用户**（安装 / 快速开始）。
>
> ⚠️ 下篇「代码地图」相对稳定；「附录·当前现状」会随开发过时 —— **以 `git log` 与代码为准**。最后更新见文末。

---

## 上篇 · 全局认知

### 1. 一句话定位 + 成熟度

> 把多 Agent 协作做成 IM 群聊体验 —— Agent 是「联系人」，对话是「工作空间」，Orchestrator 是「群里的项目经理」。

前后端分离本地运行（前端 Next.js :3000，后端 Python FastAPI :8000，PostgreSQL 主库）。经多次演进，五层架构完整落地，功能闭环已跑通。后端已集成 **用户认证与多用户隔离**（JWT + bcrypt）、**RAG 混合检索**（Milvus + ES + Neo4j）、**文件原生记忆系统**（auto_memory / auto_dream pipeline + SQLite FTS5 混合检索）、**Document + Version 知识库**、**代码图谱智能**、**执行计划工具**、**Obsidian 知识同步**、**外部 MCP 接入**、**Run 内压缩**，并保留 Electron 桌面打包 + 移动端伴随 App 脚手架。

### 2. 五层架构 + 数据流

```
L5 UI 组件（React / shadcn）            src/components/**, src/app/**
   ↑↓
L4 State + Transport                    src/stores/app-store.ts（Zustand+Immer）
   ├ Zustand normalized store           src/components/stream-provider.tsx（SSE 客户端）
   └ SSE 单连接（/api/stream）
   ↑↓
─── HTTP (REST + SSE) ─── 跨进程边界 ───
   ↑↓
L3 Application Services                  backend/app/services/
   ├ AgentRunner（per-run 生命周期）     backend/app/services/agent_runner.py ← 核心
   ├ AgentLoop（统一 Agent Loop）        backend/app/services/agent_loop.py ← 统一循环
   ├ ConversationService / EventBus
   ├ ToolExecutor（工具执行）            backend/app/tools/
   ├ RAGService（混合检索）              backend/app/services/rag_service.py
   ├ DocumentService（知识库）           backend/app/services/document_service.py
   ├ PromptAssembler（上下文组装）       backend/app/services/prompt_assembler.py
   ├ CompactPipeline（Run 内压缩）       backend/app/services/compact_pipeline.py
   ├ WorktreeService（隔离）             backend/app/services/worktree_service.py
   ├ Observability（OTel + Phoenix）     backend/app/observability/ ← 全链路追踪 + 评测
   └ EvalService（在线规则 + 离线Judge） backend/app/api/eval.py
   ↑↓
L2 Agent Platform Adapters              backend/app/adapters/
   ├ ClaudeCLIAdapter / CodexCLIAdapter (CLI 子进程路线)
   ├ CustomAdapter (SDK 路线) / MockAdapter
   ↑↓
L1 Persistence                          backend/app/db/（SQLAlchemy + PostgreSQL） + workspace 文件系统
   ↑↓
─── 基础设施层（可选, 独立降级） ───
   Milvus(向量) · Elasticsearch(全文) · Neo4j(图谱) · Kafka(事件)
   backend/app/infra/ + rag/ + memory/ + graph/ + code_intelligence/
```

**数据流主线（一次 Agent 回复）**：
用户发消息 → API 路由 → `AgentRunner` 起 run → `HookRegistry` dispatch(ON_RUN_START) → 选 `Adapter` 调 LLM → Adapter 吐 **`StreamEvent`** → 工具调用经 `execute_with_hooks`（pre/post hook 拦截）→ AgentRunner 持久化 + 经 `EventBus` 推 SSE → 前端 `stream-provider` 收事件 → `app-store` reducer 应用 → UI 重渲染 → `HookRegistry` dispatch(ON_RUN_END)。

**核心契约（改动必读对应 spec）**：
- **`StreamEvent` 联合类型**是粘合全系统的事件协议（`specs/02`）。定义在 `backend/app/schemas/events.py` + `src/shared/`。
- **Message = parts 数组**（text / thinking / tool_use / artifact_ref …），不是 markdown 字符串（`specs/03`）。
- **Artifact 独立于 Message**，有自己的生命周期与版本链（`specs/04`）。
- **所有 Agent 走统一 Agent Loop**（`specs/19`）：solo / coordinated / subagent 三种模式共用 `run_agent_loop`，任何 Agent 都能通过 `task_dispatch` 克隆自己处理子任务，Orchestrator 额外拥有 `dispatch_plan`（DAG 派发）。旧三阶段流程（plan_tasks / report_task_result / verify gate）已删除。
- **工具执行经 HookRegistry 拦截**：`execute_with_hooks` 在 pre/post 阶段分发 Hook，支持 deny/modify/inject/allow 控制流。Agent 通过 `hook_names` 字段启用特定 Hook 组。
- **Custom Agent 工具架构**：9 个 baseline 工具（fs_read/fs_write/fs_edit/fs_list/fs_glob/fs_grep/bash/ask_user/read_attachment）对所有 custom agent 必备且不可选；6 个可选工具（write_artifact/deploy_artifact/deploy_workspace/read_artifact/web_search/rag_search）由 `agent.tool_names` 增量配置。运行时合并：`baseline + tool_names + 自动注入`。
- **Guide Agent（小A）隔离**：`is_guide=True` 的 Agent 跳过 baseline 合并，只注入 7 个管理工具 + `ask_user`；非 guide agent 即使 `tool_names` 误配管理工具也会被过滤。`mode='guide'` 会话不出现在 `list_conversations`、不可删除、不出现在全局搜索。

### 3. 功能现状矩阵

| 能力 | 状态 | 说明 |
|---|---|---|
| IM 会话（多会话/搜索/置顶/归档/书签） | ✅ | 单聊 + 群聊（@mention） |
| 消息操作（引用/撤回/编辑重发/重新生成/收藏/Pin） | ✅ | Pin 注入 LLM 长期上下文 |
| ClaudeCLIAdapter | ✅ | `spawn claude -p --output-format stream-json`，CLI 自带工具与审批 |
| CodexCLIAdapter | 🔧 | `spawn codex app-server --listen stdio://`，JSON-RPC 2.0；代码就绪，端到端验证待完成 |
| CustomAgentAdapter | ✅ | OpenAI 兼容（DeepSeek/OpenAI/火山方舟）+ 自驱 tool loop（SDK 路线） |
| MockAdapter | ✅ | 开发期不烧 token |
| 自建 Agent | ✅ | 4 角色预设（coder/researcher/orchestrator/writer）· 9 baseline + 6 可选工具 · 表单/对话式创建 |
| **小A Guide Agent** | ✅ | ★ 全局悬浮助手· builtin + `is_guide=True` · 7 个管理工具 · mode='guide' 隐藏会话 · 双活跃会话模型 · 开箱即用（DEEPSEEK 兜底） |
| Orchestrator 编排 | ✅ | 统一 Agent Loop（solo/coordinated/subagent）· `task_dispatch` 克隆派发 · `dispatch_plan` DAG 调度 · 递归深度限制 · 可选计划审批 |
| 工具系统（36 个） | ✅ | write/read/update_artifact · deploy_artifact/deploy_workspace · read_attachment · fs_read/fs_write/fs_edit/fs_list/fs_glob/fs_grep/bash · code_explore · task_dispatch/dispatch_plan · create_plan/plan_step/add_plan_steps · ask_user · web_search · memory_recall/memory_store · rag_search/ingest/list/delete · load_skill/write_skill · ★ manage_agents/manage_skills/manage_mcp/manage_documents/manage_memory/manage_profile/manage_conversations（仅 guide agent） |
| Agent Skills | ✅ | custom agent 装备 skill · 渐进式披露 · `load_skill` 按需读正文 |
| Artifact 预览/编辑 | ✅ | web_app / document / image / ppt(真 .pptx 导出) / code_file / diff · 版本链 · 选区改写 · 面板内编辑 · update_artifact 增量更新 |
| Workspace 沙箱 | ✅ | sandbox/local 双模式 · fs_write 审批 · 双平台 Bash 黑名单 |
| Worktree 隔离 | ✅ | DAG 波调度并行任务用 git worktree 隔离 · 非 git 目录用目录拷贝降级 · 自动 merge-back |
| Workspace 环境隔离 | ✅ | 按会话/用户隔离环境变量 · CLI Agent HOME/USERPROFILE 按用户隔离 |
| Token 计量 | ✅ | per-run/per-message · cache 命中率 · 全局分析 |
| 跨 run 对话记忆 | ✅ | 历史序列化注入 · token 预算 · 群聊跨 agent 可见 |
| 平台抽象（Win/POSIX） | ✅ | shell 选择 · 多盘符 DirPicker · 子进程清理 |
| 全局 API Key 设置面板 | ✅ | app_settings 单行表 · 三层 key 优先级 |
| 斜杠命令菜单 | ✅ | 输入 `/` 弹命令浮层 |
| **执行计划工具** | ✅ | create_plan / plan_step / add_plan_steps · 结构化计划卡片 UI · 步骤状态实时更新 |
| **代码图谱智能** | ✅ | CodeGraph 本地运行时 · code_explore 工具 · 索引管理 · 后台同步 · 状态机 |
| **RAG 混合检索** | ✅ | Milvus(向量) + ES(全文) + Neo4j(KGStore) + RRF 融合 + Query Rewrite + Rerank |
| **文件原生记忆系统** | ✅ | auto_memory（对话→daily 卡片）+ auto_dream（daily→digest 精炼）+ SQLite FTS5 BM25 + wikilink 扩展 + RRF 融合检索 + Preference（PG KV）+ SessionMemory（会话压缩） |
| **Document + Version 知识库** | ✅ | 全局文档版本化 · 解析入库(pdfplumber→PyPDF2→pdftotext) · 按需召回 · 版本刷新 |
| **Obsidian 知识同步** | ✅ | vault 同步 · obsidian_preprocessor 预处理 · RAG 入库 |
| **外部 MCP 接入** | ✅ | MCP Server 配置管理 · client_manager · 调用审批 · mcp_bridge 暴露平台工具给 CLI agent |
| **PromptAssembler** | ✅ | 上下文组装：Profile(偏好) + Recall(记忆) + Constraints(约束) 注入 Agent |
| **Web 搜索** | ✅ | Tavily API（`web_search` 工具，需 `TAVILY_API_KEY`） |
| **Run 内压缩** | ✅ | compact_pipeline 五阶段递进压缩（ratio 阈值 0.70/0.80/0.88/0.93/0.95）· compact_markers 标记构建 · 纯结构化裁剪无 LLM |
| **生命周期 Hooks 系统** | ✅ | 7 个内置 Hook（审计/压缩/检查点/记忆/技能/摘要/审批）· 10 个生命周期事件 · Agent 按 `hook_names` 启用 |
| **双活跃会话模型** | ✅ | ★ 工作会话（activeConversationId）+ guide 会话（guideConversationId）并行 · GuideFloatingPanel 悬浮组件 · 拖拽/缩放/收起/快捷键 · localStorage 持久化 · 移动端全屏 |
| **Checkpoint 检查点** | ✅ | SDK Agent turn 级检查点保存与恢复（`agent_run_checkpoints` 表）|
| **统一转录渲染** | ✅ | transcript_renderer 统一消息流渲染逻辑 |
| Electron 桌面版 | ⚠️ | 打包脚本就绪；内嵌 Next 已无后端，需改启 Python |
| 移动端伴随 App | ⏳ | 响应式 Web 已适配；Capacitor 原生壳脚手架已建，配对通信待打通 |
| **用户认证与多用户隔离** | ✅ | JWT(access 1h + refresh 7d) + bcrypt · 登录/注册页 · auth-gate · 个人资料弹窗 · VIP 快捷登录 · CSRF 防护 · 所有用户数据表 `user_id` 隔离 |
| ~~Redis 元数据缓存 + 异步 DB~~ | ❌ 已移除 | 双 DB 架构下 SQLite 直写 + 进程内 dict TTL 缓存替代 |
| **记忆管理 UI** | ✅ | 记忆库面板 · 长期记忆/偏好/短期记忆三面板 · 查看/删除/固化 |
| **Agent 可观测性与评测** | ✅ | OpenTelemetry 全链路追踪(Level 4 深度埋点) · Arize Phoenix(:6006) · 在线规则评测(默认开) · 离线 LLM-as-Judge(手动触发) · 5+4 维评测指标体系 |
| **Thinking/Tool 耗时 UI** | ✅ | 实时显示思考与工具调用耗时 |
| 测试覆盖 | 🟡 | 后端 pytest（141 测试文件, ruff 全绿）；前端 Vitest 纯函数；E2E 待补 |

---

## 下篇 · 代码地图（功能 → 文件）

> 路径相对仓库根。找某功能从这里定位，不用全局搜索。

### 入口 & 前端壳
| 关注点 | 文件 |
|---|---|
| App 入口 / 布局 | `src/app/page.tsx` · `src/app/layout.tsx` |
| 认证页面 | `src/app/login/page.tsx` · `src/app/register/page.tsx` |
| 认证守卫 | `src/components/auth-gate.tsx` · `src/stores/auth-store.ts` |
| SSE 全局连接（客户端） | `src/components/stream-provider.tsx` |
| 前端状态总线（Zustand+Immer） | `src/stores/app-store.ts` |
| API base 配置 | `src/lib/config.ts`（读 `NEXT_PUBLIC_API_BASE_URL`） |
| REST 客户端 | `src/lib/api.ts` · `src/lib/api/memory.ts` |
| 主题 | `src/components/theme-provider.tsx` · `theme-toggle.tsx` |

### L5 UI 组件（`src/components/`）
| 区域 | 文件 |
|---|---|
| 侧栏（会话/产物库/Agents/知识库/Skills/分析 Tab） | `sidebar.tsx` |
| 聊天主面板 | `chat-panel.tsx` · `message-list.tsx` · `message-item.tsx` · `message-parts.tsx` · `message-highlight-layer.tsx` · `turn-timeline.tsx` · `wave-column-header.tsx` |
| 输入框（附件/审批/选区引用/斜杠命令） | `message-input.tsx` · `edit-message-input.tsx` |
| Orchestrator 调度卡 | `dispatch-plan-card.tsx` |
| 产物预览 / 产物库 | `artifact-preview-panel.tsx` · `artifact-library.tsx` · `artifact-code-editor.tsx` |
| 知识库 / 文档 | `knowledge-library.tsx` · `document-detail.tsx` · `document-version-item.tsx` · `upload-document-dialog.tsx` |
| Skills 库 | `skill-library.tsx` |
| 全局搜索 | `global-search.tsx` · `global-search-trigger.tsx` · `search-result-item.tsx` |
| fs_write 审批面板 + diff | `pending-writes-panel.tsx` · `pending-write-diff-tab.tsx` · `diff-block.tsx` · `diff-viewer-styles.ts` |
| bash 命令审批 | `pending-bash-commands-panel.tsx` |
| MCP 调用审批 | `pending-mcp-call-card.tsx` · `mcp-server-library.tsx` · `mcp-server-edit-dialog.tsx` |
| 代码图谱控制 | `code-intelligence-control.tsx` · `code-intelligence-switch.tsx` |
| ask_user 结构化弹窗 | `ask-user-question-dialog.tsx` |
| Token 计量 | `usage-dashboard.tsx` · `usage-badge.tsx` |
| 文件浏览器 | `file-explorer-panel.tsx` · `file-tab.tsx` · `file-library-dialog.tsx` |
| 选区改写 / 引用 | `selection-popover.tsx` · `quoted-message.tsx` |
| 导航辅助 | `pinned-messages-bar.tsx` · `conversation-outline.tsx` |
| Agent 库 / 创建 | `agent-library.tsx` · `create-agent-dialog.tsx` · `add-agent-dialog.tsx` · `agent-create-wizard.tsx` · `agent-avatar.tsx` · `agent-info-popover.tsx` · `agent-working-indicator.tsx` |
| 会话创建 / 目录选择 | `new-conversation-dialog.tsx` · `dir-picker-dialog.tsx` |
| ★ 小A 全局悬浮助手 | `guide-floating-panel.tsx`（拖拽/缩放/收起/快捷键 · 精简 MessageList + MessageInput · ask_user 内联渲染 · 移动端全屏） |
| 设置面板 | `settings-dialog.tsx` |
| 个人资料 | `profile-dialog.tsx` |
| 认证品牌面板 | `auth-brand-panel.tsx` · `particle-background.tsx` |
| 记忆管理 | `memory-library.tsx` · `settings/memory-management/long-term-memory-panel.tsx` · `settings/memory-management/preference-panel.tsx` · `settings/memory-management/session-memory-panel.tsx` |
| 斜杠命令 | `slash-command-menu.tsx` · `slash-command-help-dialog.tsx` |
| Workspace 环境提示 | `workspace-env-hint-card.tsx` |
| 渲染基建 | `markdown.tsx` · `code-block.tsx` · `attachment-chip.tsx` · `ui/*`（shadcn） |

### L3→L2 API 路由（`backend/app/api/`）
| 端点文件 | 作用 |
|---|---|
| `stream.py` | **SSE 全局事件流**（一条连接） |
| `conversations.py` | 会话 CRUD · 消息发送 · regenerate · compact |
| `messages.py` | 消息操作（edit/pin/bookmark/withdraw） |
| `agents.py` | Agent CRUD（含 Skills 配置 + draft 对话式创建） |
| `artifacts.py` | 产物 CRUD · 版本 · 导出 |
| `attachments.py` | 附件上传 |
| `fs.py` | workspace 文件 listdir/read/write |
| `pending.py` | 审批中转（writes/questions/bash/dispatch/mcp） |
| `settings.py` | 全局设置 / API key |
| `auth.py` | ★ 用户认证（注册/登录/刷新/登出 · JWT HttpOnly cookie · VIP 快捷登录） |
| `profile.py` | ★ 用户资料管理（显示名称/头像） |
| `runs_misc.py` | run 中止 / usage summary |
| `documents.py` | ★ Document + Version 知识库 CRUD |
| `skills.py` | Skills 上传 / 列表 / 加载 |
| `eval.py` | ★ Agent 评测（`POST /api/eval/judge/{trace_id}` 手动触发 LLM-as-Judge） |
| `deployments.py` | 本地静态发布预览 URL |
| `code_intelligence.py` | ★ 代码图谱智能（启用/同步/重建索引 · 状态查询） |
| `mcp.py` | ★ 外部 MCP Server 配置管理（CRUD · 调用） |
| `memory.py` | ★ 记忆管理（长期记忆/偏好/短期记忆 查询/删除/固化） |
| `obsidian.py` | ★ Obsidian vault 同步 |
| `plan_usage.py` | ★ 执行计划用量统计 |
| `workspaces.py` | ★ workspace 管理（环境隔离配置） |
| `mobile/routes.py` | 移动端伴随 API（配对 / 远程审批） |

### L3 服务层（`backend/app/services/`）
| 服务 | 文件 | 职责 |
|---|---|---|
| **AgentRunner** | `agent_runner.py` | per-run 生命周期、选 adapter、`build_adapter_input`、历史注入、token 预算、baseline 工具合并 —— **L3 核心** |
| **AgentLoop** | `agent_loop.py` | ★ `run_agent_loop`（solo/coordinated/subagent 三模式统一循环）· `spawn_subagent_loop`（递归子 Agent 派发）· prompt builders |
| DAG 执行器 | `dag_executor.py` | ★ `validate_dag` / `topological_waves` / `execute_dag`（DAG 验证 + 波调度 + 并行执行） |
| Worktree 隔离 | `worktree_service.py` | ★ DAG 波调度并行任务的 git worktree 隔离（创建→merge-back→清理）· 非 git 目录拷贝降级 |
| Workspace 环境隔离 | `workspace_env_service.py` | ★ 按会话/用户隔离环境变量 |
| Orchestrator stub | `orchestrator.py` · `orchestrator_prompts.py` | 旧三阶段已移除，仅保留 stub 与工具函数 |
| 会话服务 | `conversation_service.py` | 会话/消息持久化 |
| 跨 run 上下文 | `conversation_context.py` | MessagePart → ChatMessage 序列化、pinned 注入 |
| 上下文压缩 | `context_compaction_service.py` | 手动压缩历史为摘要 |
| ★ Run 内压缩 | `compact_pipeline.py` · `compact_markers.py` | ★ 五阶段递进压缩（ratio 阈值 0.70/0.80/0.88/0.93/0.95）· 纯结构化裁剪无 LLM |
| ReAct 终止 | `react_loop_termination.py` | ★ ReAct loop 终止逻辑（stage 4 软收尾 + stage 5 强制终止） |
| 统一转录渲染 | `transcript_renderer.py` | ★ 统一消息流渲染逻辑 |
| 事件总线 | `event_bus.py` | asyncio.Queue 扇出，推 SSE |
| 产物服务 | `artifact_service.py` · `deployment_service.py` | 产物 CRUD + 版本链 · 本地静态发布与下载包 |
| Agent / 附件 / 文件 | `agent_runner` 内联 · `attachment_service.py` · `fs_service.py` | |
| 审批中转 store | `pending_writes.py` · `pending_questions.py` · `pending_bash_commands.py` · `pending_dispatch_plans.py` · `pending_mcp_calls.py` · ★ `pending_merge_conflicts.py`（Worktree 冲突审批） | |
| bash 命令审批 | `bash_command_approval.py` | bash 命令审批逻辑 |
| 设置 / Key | `settings_service.py` · `global_settings_service.py` | 三层 key 优先级解析 · 全局设置缓存（进程内 dict TTL） |
| ★ 异步 DB 写入 | `async_db_writer.py` | ★ 已移除 (Redis Stream write-behind 废弃，双 DB 架构直写 SQLite) |
| ★ 崩溃恢复 | `recovery_scan.py` | 启动时扫描 `status=streaming` 的消息，标记为 interrupted（SQLite WAL 自带崩溃恢复） |
| **★ 可观测性** | `observability/` | OTel 全链路追踪（Level 4 深度埋点）· span 中英文映射 · ★ run_collector per-run 内存 span 收集 · 在线规则评测 · 离线 LLM-as-Judge · shutdown 清理 |
| 搜索 | `search_service.py` | 消息全文搜索 |
| runner 注册 | `runner_registry.py` | per-conversation runner 生命周期 |
| 部署命令 | `deploy_command_service.py` | 部署斜杠命令 |
| Token 分析 | `usage_summary_service.py` | Token 用量聚合 |
| 网络发现 | `network_hints.py` | 移动端 LAN/Tailscale 发现 |
| **RAG 服务** | `rag_service.py` | ★ 混合检索（Milvus+ES+KG+RRF）+ ingest + delete |
| **Document 服务** | `document_service.py` | ★ Document + Version CRUD + 入库 RAG |
| **Obsidian 同步** | `obsidian_sync_service.py` | ★ Obsidian vault 同步 + 预处理 |
| **PromptAssembler** | `prompt_assembler.py` | ★ 上下文组装（Profile + Recall + Constraints） |
| Skill 服务 | `skill_service.py` | Skills 加载 / 写入 |
| **HookRegistry** | `hook_registry.py` | ★ 生命周期 Hook 注册与分发（10 个事件） |
| 内置 Hooks | `hooks/`（7 个） | audit_log · auto_compact · checkpoint · memory_persist · skill_auto_activator · summary_generate · tool_approval |
| Checkpoint | `checkpoint_service.py` | SDK Agent turn 级检查点保存/恢复 |
| ★ Guide Agent prompt | `guide_prompt.py` | ★ 小A system prompt（管理边界、确认规则、记忆整理规则、交互风格） |
| 执行计划 | `plan_registry.py` · `plan_dispatch_mapping.py` · `plan_usage_service.py` | ★ 计划注册/查询 · 计划→派发映射 · 计划用量统计 |
| 项目产物 | `project_artifact.py` | 项目级产物管理 |
| Agent 负载 | `agent_load_tracker.py` | Agent 负载追踪 |

### L2 适配器（`backend/app/adapters/`）
| 文件 | 说明 |
|---|---|
| `base.py` | `AdapterInput` + ABC + `AdapterName`（事件流契约，`specs/05`） |
| `registry.py` | adapter 注册/选择（注册 Mock / Custom / ClaudeCLI / CodexCLI） |
| `cli_base.py` | ★ CLI 适配器公共基类：子进程生命周期、stdin/stdout 管道、超时/取消、参数过滤、环境变量合并 |
| `conpty.py` | Windows ConPTY 支持（隐藏子进程窗口、伪终端） |
| `_delta_flusher.py` | ★ 增量刷新器（流式 delta 批量刷新） |
| `claude_adapter.py` | ★ ClaudeCLIAdapter：`spawn claude` stream-json 协议，CLI 自带工具 |
| `codex_adapter.py` | ★ CodexCLIAdapter：`spawn codex app-server` JSON-RPC 2.0 通信 |
| `custom_adapter.py` | OpenAI 协议 stream + 自驱 tool loop（model-done 主路径，SDK 路线） |
| `custom_provider_client.py` / `session_store.py` | provider 客户端 / 会话存储 |
| `mock_adapter.py` | 假事件流，开发用 |
> MCP Bridge：`backend/app/mcp_bridge.py` — stdio MCP Server，把 `write_artifact`/`ask_user`/`task_dispatch` 等 AChat 平台工具暴露给 CLI agent（Claude/Codex CLI 通过 `--mcp-config` 拉起）。

### 认证模块（`backend/app/auth/`）
| 文件 | 职责 |
|---|---|
| `jwt_handler.py` | JWT 生成/验证（access 1h + refresh 7d） |
| `password.py` | bcrypt 密码哈希（cost factor 12） |
| `service.py` | 认证业务逻辑（注册/登录/刷新/登出/VIP 快捷登录） |
| `dependencies.py` | FastAPI 依赖注入（获取当前用户 · token_version 校验） |
| `ownership.py` | 资源所有权检查（user_id 隔离） |

### 代码图谱智能（`backend/app/code_intelligence/`）
| 文件 | 职责 |
|---|---|
| `runtime.py` | CodeGraph 运行时管理（下载/解析/版本匹配） |
| `index_manager.py` | 索引管理（启用/同步/重建） |
| `service.py` | 后台编排（异步任务 + 防抖同步） |
| `process_runner.py` | CodeGraph 命令执行器 |
| `state_machine.py` | 索引状态机（状态转换约束） |
| `bootstrap.py` | 启动初始化 |
| `debounce.py` | ReadySync 防抖器 |
| `metadata.py` | 元数据存储（符号计数等） |
| `progress.py` | 进度回调 |

### MCP 客户端（`backend/app/mcp/`）
| 文件 | 职责 |
|---|---|
| `client_manager.py` | ★ 外部 MCP Server 连接管理（stdio/SSE 传输 · 工具发现 · 调用代理） |

### 工具系统（`backend/app/tools/`）
`base.py`（ToolContext + ToolDef） · `registry.py`（注册 36 个工具） · `write_artifact.py` · `read_artifact.py` · `update_artifact.py` · `deploy_artifact.py` · `deploy_workspace.py` · `read_attachment.py` · `fs_read.py` · `fs_write.py` · `fs_edit.py` · `fs_list.py` · `fs_glob.py` · `fs_grep.py` · `bash.py` · `code_explore.py`（代码图谱探索） · `task_dispatch.py`（子 Agent 派发） · `dispatch_plan.py`（DAG 派发） · `execution_plan.py`（create_plan/plan_step/add_plan_steps 执行计划） · `ask_user.py` · `web_search.py` · `memory_rag.py`（memory_recall + rag_search/ingest/list/delete） · `memory_store.py`（主动记忆存储） · `skills.py`（load_skill/write_skill） · ★ `manage_base.py`（管理工具公共基类） · ★ `manage_agents.py` / `manage_skills.py` / `manage_mcp.py` / `manage_documents.py` / `manage_memory.py` / `manage_profile.py` / `manage_conversations.py`（7 个 guide agent 专用管理工具） · `rate_limiter.py`。详见 `specs/07`。

### RAG 引擎（`backend/app/rag/`）
| 文件 | 职责 |
|---|---|
| `rag_engine.py` | HybridStore：向量(Milvus) + 全文(ES) + 图谱(KG) + RRF 融合 |
| `parser.py` | 文档解析（pdfplumber → PyPDF2 → pdftotext 三级降级） |
| `splitter.py` | 文档分块（chunk_size / overlap 可配） |
| `rewriter.py` | Query Rewriting（LLM 生成扩展查询） |
| `reranker.py` | Reranking（LLM 打分重排） |
| `obsidian_preprocessor.py` | ★ Obsidian vault 预处理（wikilink 解析 · frontmatter 提取） |

### 记忆系统（`backend/app/memory/`）
| 文件 | 职责 |
|---|---|
| `memory_service.py` | ★ 门面：file-native pipeline (auto_memory + auto_index + auto_dream + proactive) + Preference + SessionMemory |
| `file_store/` | Markdown 文件读写 + frontmatter + wikilinks + workspace 目录管理 |
| `search/` | SQLite FTS5 BM25 + wikilink 图扩展 + RRF 融合检索 |
| `pipeline/` | auto_memory + auto_index + auto_dream + proactive |
| `preference.py` | 用户偏好（PG KV 表，保留不动） |
| `session_memory.py` | 会话摘要（上下文压缩，保留不动） |
| `memory_writer_compat.py` | Preference 提取工具（从旧 memory_writer 保留） |
### 知识图谱（`backend/app/graph/`）
| 文件 | 职责 |
|---|---|
| `kgstore.py` | KGStore：文档 → 实体/关系抽取 → Neo4j 入图 → 子图检索 |
| `extractor.py` | LLM 驱动的实体/关系抽取 |
| `types.py` | 图谱类型定义 |

### 基础设施层（`backend/app/infra/`）
| 文件 | 职责 |
|---|---|
| `factory.py` | `build_infrastructure()`：配置驱动，独立降级（Milvus/ES/Neo4j/Kafka · ~~Redis~~ 已移除） |
| `hybrid.py` | HybridStore 抽象（向量 + 全文 + 图谱统一接口） |
| `cache.py` | ★ 已移除（no-op stub，Redis KV 缓存被进程内 dict TTL 替代） |
| `cache_helpers.py` | ★ 缓存实体查找（Agent/Settings/Workspace/GlobalSettings cached） |
| `cache_metrics.py` | 嵌入缓存命中率指标 |
| `status.py` | 基础设施连接状态面板 |

### 可观测性层（`backend/app/observability/`）
| 文件 | 职责 |
|---|---|
| `tracer.py` | ★ OTel TracerProvider 生命周期（BatchSpanProcessor + OTLPSpanExporter → Phoenix） |
| `instrumentation.py` | ★ `@traced` 装饰器 + 属性 key 常量（`agenthub.` 前缀） |
| `span_names.py` | ★ span 中英文映射表（`agent.run · 代理运行`） |
| `eval_rules.py` | ★ 在线规则评测（14 指标：任务完成率/工具成功率/轮次效率/token 消耗/派发深度等） |
| `eval_judge.py` | ★ 离线 LLM-as-Judge 评测（9 维度：工具选择/子任务粒度/聚合忠实度等） |
| `eval_metrics.py` | 评测指标体系定义（Agent 全过程 5 维度 + 多 Agent 协作 4 维度） |

### L1 持久化（`backend/app/db/`）
| 文件 | 说明 |
|---|---|
| `models.py` | **19 张表**：14 核心（users/agents/conversations/messages/artifacts/workspaces/attachments/agent_runs/agent_run_checkpoints/context_summaries/app_settings/global_settings/user_settings/mcp_servers）+ 3 AGI-memory（user_preferences/rag_chunks/chat_history）+ 2 Document（documents/document_versions）。记忆系统已迁移到文件原生（Markdown + SQLite FTS5），不再使用 long_term_memory/memory_nodes/memory_edges 表 |
| `table_routing.py` | ★ 双 DB 表路由（10 张本地 SQLite + 12 张远端 PG） |
| `engine.py` | ★ 双引擎：本地 SQLite[WAL] + 远端 PostgreSQL（连接池） |
| `__init__.py` | 模块导出 |

DB 文件：双 DB 架构（本地 SQLite[WAL] 承载对话热数据 + 远端 PostgreSQL 承载用户系统与知识/RAG 数据，`docker-compose.infra.yml` 启动 PG）；workspace：`.agenthub-data/users/<user_id>/workspaces/<conv_xxx>/`（多用户隔离）。

### 共享类型（`src/shared/`）
`types.ts`（**`StreamEvent` / `MessagePart` 等跨层类型，改动牵一发动全身**） · `constants.ts` · `model-registry.ts` · `ppt-theme.ts` · `codex-compat.ts` · `openai-compatible.ts` · `agent-builder-config.ts`（★ 4 角色预设 + baseline 工具配置） · `agent-icons.ts` · `artifact-version-diff.ts` · `mermaid-normalize.ts` · `ppt-normalize.ts` · `usage.ts` 等 18 个文件。前端纯类型，与后端 `backend/app/schemas/` 保持 camelCase 兼容。

### 桌面（`electron/`）& 移动（`apps/mobile/`）
- Electron：`main.ts`（主进程） · `paths.ts`（userData 路径迁移） · `server-bootstrap.ts`。`specs/12`。
- 移动：`apps/mobile/`（Capacitor 伴随客户端，monorepo workspace `@agenthub/mobile`）。`specs/14`。

### 测试
- 后端：`backend/tests/`（pytest，141 测试文件，`asyncio_mode = "auto"`，ruff 全绿；含 auth/CSRF/SSE 认证/隔离/异步写入/恢复扫描/压缩管线/worktree 测试）。
- 前端单元：`src/**/*.test.ts` / `src/**/*.test.tsx`（Vitest 纯函数）。

---

## 附 · 当前现状（易过时，以 git 为准）

### ✅ 近期完成
- **★ 小A Guide Agent（全局悬浮助手）**：builtin + `is_guide=True` Agent（`ag_guide_builtin`）· 启动种子机制（幂等）· `guide_prompt.py` 约束管理边界 · 7 个管理工具（manage_agents/skills/mcp/documents/memory/profile/conversations）· `manage_memory(action=optimize)` LLM 驱动智能记忆整理 · `mode='guide'` 隐藏会话（不出现在 list/搜索/不可删）· `GuideSideEffectEvent` 副作用事件 · `GuideFloatingPanel` 悬浮组件（拖拽/缩放/收起/`Ctrl/Cmd+G` 快捷键/移动端全屏）· 双活跃会话模型（工作 + guide 并行）· 开箱即用（`GUIDE_AGENT_*` 环境变量配置，默认 deepseek 兜底）
- **Agent 角色预设重设**：9 个预设推翻重设为 4 个（coder/researcher/orchestrator/writer）· 引入 `BASELINE_AGENT_TOOLS`（9 个工具对所有 custom agent 必备，UI 不可选）· UI 可选工具从 14 个缩减为 5 个 · systemPromptTemplate 职责收窄 · 修复 `_build_agent_hub_tool_guidance` 的 has_file_tools 块 bug
- **代码图谱智能系统**：CodeGraph 本地运行时管理 · `code_explore` 工具 · 索引管理（启用/同步/重建）· 后台异步编排 + 防抖同步 · 状态机 · 前端控制开关
- **执行计划工具**：`create_plan` / `plan_step` / `add_plan_steps` 三个工具 · 结构化计划卡片 UI · 步骤状态实时更新 · plan_registry/plan_dispatch_mapping/plan_usage_service 服务支撑
- **Run 内压缩（五阶段 pipeline）**：`compact_pipeline.py` 递进压缩（ratio 阈值 0.70/0.80/0.88/0.93/0.95）· `compact_markers.py` 标记构建 · 纯结构化裁剪无 LLM · `react_loop_termination.py` stage 4/5 终止逻辑
- **Worktree 隔离**：DAG 波调度并行任务用 git worktree 隔离 · 非 git 目录用目录拷贝降级 · 自动 merge-back · 冲突标记
- **Workspace 环境隔离**：`workspace_env_service.py` 按会话/用户隔离环境变量
- **统一转录渲染器**：`transcript_renderer.py` 统一消息流渲染逻辑
- **Obsidian 知识同步**：vault 同步 · `obsidian_preprocessor.py` 预处理（wikilink 解析 · frontmatter 提取）· RAG 入库
- **外部 MCP 接入**：`mcp/client_manager.py` 外部 MCP Server 连接管理 · 配置 CRUD · 调用审批 · `pending_mcp_calls.py` 审批中转
- **update_artifact 工具**：增量更新已有产物（无需创建新版本）
- **memory_store 工具**：Agent 主动存储记忆
- **会话记忆层**：`session_memory.py` 跨 run 会话级上下文
- **VIP 快捷登录**：服务端默认密码重置 · VIP 登录端点 · 前端快捷对话框
- **Thinking/Tool 耗时 UI**：实时显示思考与工具调用耗时
- **Agent 可观测性与评测系统**：OpenTelemetry SDK 全链路采集（FastAPI/httpx/openai 自动 instrumentation + Level 4 深度手动埋点 18 处）· OTLP gRPC 发送至 Arize Phoenix（独立 Docker :6006）· 在线规则评测（默认开启，14 指标自动从 trace 计算）· 离线 LLM-as-Judge（默认关闭，手动触发 `POST /api/eval/judge/{trace_id}`）· Agent 全过程评测（5 维度）· 多 Agent 协作评测（4 维度）· `trace_enabled` 开关（关闭后全 no-op）
- **用户认证与多用户隔离**：JWT(access 1h + refresh 7d) + bcrypt 密码哈希 · 登录/注册页面 · auth-gate 路由保护 · 个人资料弹窗 · CSRF 防护(Origin header) · SSE 连接认证 · 所有用户数据表 `user_id` 隔离 · builtin agent `user_id IS NULL` 共享 · CLI Agent `HOME`/`USERPROFILE` 按用户隔离
- **Redis 元数据缓存 + 异步 DB 写入**：~~已移除~~ — 双 DB 架构下 SQLite 直写 + 进程内 dict TTL 缓存替代
- **记忆管理 UI**：记忆库面板 · 长期记忆/偏好/短期记忆三面板 · 查看/删除/固化操作
- **统一 Agent Loop**（spec 19）：solo / coordinated / subagent 三模式统一为 `run_agent_loop` while-loop，移除旧三阶段 Orchestrator
- **通用子任务派发**：任何 Agent 都能通过 `task_dispatch` 克隆自己处理子任务（clone-self，`hidden` 消息），`MAX_DISPATCH_DEPTH=3` 递归深度限制
- **DAG 派发计划**：`dispatch_plan` 工具声明结构化 DAG，`dag_executor.py` 做拓扑排序 + 波调度 + 并行执行 + 级联跳过，可选计划审批
- **生命周期 Hooks 系统**：7 个内置 Hook · 10 个生命周期事件 · Agent 按 `hook_names` 启用
- **Checkpoint 检查点**：SDK Agent turn 级检查点保存与恢复
- **RAG 混合检索系统**：Milvus(向量) + Elasticsearch(全文) + Neo4j(KGStore) 三路召回 + RRF 融合 + Query Rewrite + Rerank
- **文件原生记忆系统**：auto_memory + auto_dream pipeline + SQLite FTS5 BM25 + wikilink 扩展 + RRF 融合 + Preference(PG KV) + SessionMemory
- **Document + Version 知识库**：全局文档版本化 · 解析入库 · 按需召回 · 版本刷新三能力
- **PromptAssembler**：Profile + Recall + Constraints 上下文组装
- **PostgreSQL 迁移**：从 SQLite 迁移到 PostgreSQL 16（asyncpg），22 张表
- **PPT 产物**：ppt 类型 + 真 .pptx 导出 + 完整 theme token

### 🔧 适配器接入路线图

| 适配器 | 路线 | 接入状态 | 说明 |
|---|---|---|---|
| Claude CLI | CLI 子进程 | ✅ 已接入，修 bug 中 | stream-json 协议；MCP bridge 打通平台工具；Windows 环境变量/MCP 工具名前缀已修 |
| Codex CLI | CLI 子进程 | 🔧 代码就绪，验证待完成 | JSON-RPC 2.0；`codex_adapter.py` 已实现，端到端测试与联调待补 |
| Custom | SDK | ✅ 已实现 | OpenAI 兼容 API + 自驱 tool loop + baseline 工具合并 |
| Hermes | CLI 子进程 | ⏳ 待接入 | 规划中 |
| OpenClaw | CLI 子进程 | ⏳ 待接入 | 规划中 |
| OpenCode | CLI 子进程 | ⏳ 待接入 | 规划中 |

> 迁移方案见 `openspec/changes/migrate-claude-codex-to-cli/`。CLI 路线将厂商 CLI 作为子进程拉起，工具执行/沙箱/审批由 CLI 自管，AChat 仅翻译事件流。

### 📋 待办
- OpenSpec 主 specs 同步（orchestrator / tools / stream-events / persistence / core-domain 需更新以反映统一 Agent Loop）
- OpenSpec 主 specs 同步（persistence / platform-security / frontend 需更新以反映用户认证与多用户隔离）
- OpenSpec 主 specs 同步（persistence 需更新以反映双 DB 架构 + Redis 移除）
- Electron 桌面版改为启动 Python 后端（当前内嵌 Next 已无后端）
- 移动端伴随 App 配对通信打通
- E2E 测试补充（产物预览/导出 + 群聊调度，需测试假 adapter）
- Codex CLI 适配器端到端联调与测试（代码已就绪）
- Hermes / OpenClaw / OpenCode 适配器接入
- 旧编号 specs（01/07/08）逐步迁移到 OpenSpec 体系

### ⚠️ 关键约定（动手前必看）
- 改实体字段 → 同步 `specs/01` + `backend/app/db/models.py`；改事件 → `specs/02` + `backend/app/schemas/events.py` + `src/shared/`；改 Bash 黑名单 → 同步 `specs/11` + `backend/app/utils/` 安全模块。
- 所有 LLM 调用必带取消机制（`asyncio.Event`）；跨进程输入必经 Pydantic 校验；fs/bash 必过 Workspace 沙箱。
- 基础设施客户端**不在 L3 直接 new**，必须经 `backend/app/infra/factory.py`。
- 后端 async 函数调用必须 `await`。
- 完整协作规则见 [CLAUDE.md](./CLAUDE.md)。

---

*最后更新：2026-07-30 · 同步小A Guide Agent（全局悬浮助手 + 7 个管理工具 + 双活跃会话模型）、Agent 角色预设重设、代码图谱智能、执行计划工具、Run 内压缩五阶段 pipeline、Worktree 隔离、Obsidian 同步、外部 MCP 接入、统一转录渲染等近期功能。改动较大后请同步本文件的「功能矩阵」与「当前现状」两节。*
