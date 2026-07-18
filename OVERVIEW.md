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

前后端分离本地运行（前端 Next.js :3000，后端 Python FastAPI :8000，PostgreSQL 主库）。经多次演进，五层架构完整落地，功能闭环已跑通。后端已集成 **用户认证与多用户隔离**（JWT + bcrypt）、**RAG 混合检索**（Milvus + ES + Neo4j）、**分层记忆系统**、**Document + Version 知识库**、**Redis 元数据缓存 + 异步 DB 写入**，并保留 Electron 桌面打包 + 移动端伴随 App 脚手架。

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
   └ PromptAssembler（上下文组装）       backend/app/services/prompt_assembler.py
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
   Milvus(向量) · Elasticsearch(全文) · Neo4j(图谱) · Kafka(事件) · Redis(缓存+异步写)
   backend/app/infra/ + rag/ + memory/ + graph/
```

**数据流主线（一次 Agent 回复）**：
用户发消息 → API 路由 → `AgentRunner` 起 run → `HookRegistry` dispatch(ON_RUN_START) → 选 `Adapter` 调 LLM → Adapter 吐 **`StreamEvent`** → 工具调用经 `execute_with_hooks`（pre/post hook 拦截）→ AgentRunner 持久化 + 经 `EventBus` 推 SSE → 前端 `stream-provider` 收事件 → `app-store` reducer 应用 → UI 重渲染 → `HookRegistry` dispatch(ON_RUN_END)。

**核心契约（改动必读对应 spec）**：
- **`StreamEvent` 联合类型**是粘合全系统的事件协议（`specs/02`）。定义在 `backend/app/schemas/events.py` + `src/shared/`。
- **Message = parts 数组**（text / thinking / tool_use / artifact_ref …），不是 markdown 字符串（`specs/03`）。
- **Artifact 独立于 Message**，有自己的生命周期与版本链（`specs/04`）。
- **所有 Agent 走统一 Agent Loop**（`specs/19`）：solo / coordinated / subagent 三种模式共用 `run_agent_loop`，任何 Agent 都能通过 `task_dispatch` 克隆自己处理子任务，Orchestrator 额外拥有 `dispatch_plan`（DAG 派发）。旧三阶段流程（plan_tasks / report_task_result / verify gate）已删除。
- **工具执行经 HookRegistry 拦截**：`execute_with_hooks` 在 pre/post 阶段分发 Hook，支持 deny/modify/inject/allow 控制流。Agent 通过 `hook_names` 字段启用特定 Hook 组。

### 3. 功能现状矩阵

| 能力 | 状态 | 说明 |
|---|---|---|
| IM 会话（多会话/搜索/置顶/归档/书签） | ✅ | 单聊 + 群聊（@mention） |
| 消息操作（引用/撤回/编辑重发/重新生成/收藏/Pin） | ✅ | Pin 注入 LLM 长期上下文 |
| ClaudeCLIAdapter | ✅ | `spawn claude -p --output-format stream-json`，CLI 自带工具与审批；接入中修 bug |
| CodexCLIAdapter | 🔧 | `spawn codex app-server --listen stdio://`，JSON-RPC 2.0；代码就绪，端到端验证待完成 |
| CustomAgentAdapter | ✅ | OpenAI 兼容（DeepSeek/OpenAI/火山方舟）+ 自驱 tool loop（SDK 路线） |
| MockAdapter | ✅ | 开发期不烧 token |
| 自建 Agent | ✅ | 表单/对话式创建，自定义 prompt + 工具集 + Skills |
| Orchestrator 编排 | ✅ | 统一 Agent Loop（solo/coordinated/subagent）· `task_dispatch` 克隆派发 · `dispatch_plan` DAG 调度 · 递归深度限制 · 可选计划审批 |
| 工具系统（24 个） | ✅ | write/read/deploy_artifact · read_attachment · fs_read/fs_write/fs_edit/fs_list/fs_glob/fs_grep/bash · task_dispatch · dispatch_plan · ask_user · web_search · memory_recall · rag_search/ingest/list/delete · load_skill/write_skill |
| Agent Skills | ✅ | custom agent 装备 skill · 渐进式披露 · `load_skill` 按需读正文 |
| Artifact 预览/编辑 | ✅ | web_app / document / image / ppt(真 .pptx 导出) / code_file / diff · 版本链 · 选区改写 · 面板内编辑 |
| Workspace 沙箱 | ✅ | sandbox/local 双模式 · fs_write 审批 · 双平台 Bash 黑名单 |
| Token 计量 | ✅ | per-run/per-message · cache 命中率 · 全局分析 |
| 跨 run 对话记忆 | ✅ | 历史序列化注入 · token 预算 · 群聊跨 agent 可见 |
| 平台抽象（Win/POSIX） | ✅ | shell 选择 · 多盘符 DirPicker · 子进程清理 |
| 全局 API Key 设置面板 | ✅ | app_settings 单行表 · 三层 key 优先级 |
| 斜杠命令菜单 | ✅ | 输入 `/` 弹命令浮层 |
| **RAG 混合检索** | ✅ | Milvus(向量) + ES(全文) + Neo4j(KGStore) + RRF 融合 + Query Rewrite + Rerank |
| **分层记忆系统** | ✅ | STM(短期) + LTM(长期, embedding 召回) + Preference(偏好) + GraphMemory(图谱) + 自动固化/衰减 |
| **Document + Version 知识库** | ✅ | 全局文档版本化 · 解析入库(pdfplumber→PyPDF2→pdftotext) · 按需召回 · 版本刷新 |
| **PromptAssembler** | ✅ | 上下文组装：Profile(偏好) + Recall(记忆) + Constraints(约束) 注入 Agent |
| **Web 搜索** | ✅ | Tavily API（`web_search` 工具，需 `TAVILY_API_KEY`） |
| **生命周期 Hooks 系统** | ✅ | 7 个内置 Hook（审计/压缩/检查点/记忆/技能/摘要/审批）· 10 个生命周期事件 · Agent 按 `hook_names` 启用 |
| **Checkpoint 检查点** | ✅ | SDK Agent turn 级检查点保存与恢复（`agent_run_checkpoints` 表）|
| Electron 桌面版 | ⚠️ | 打包脚本就绪；内嵌 Next 已无后端，需改启 Python |
| 移动端伴随 App | ⏳ | 响应式 Web 已适配；Capacitor 原生壳脚手架已建，配对通信待打通 |
| **用户认证与多用户隔离** | ✅ | JWT(access 1h + refresh 7d) + bcrypt · 登录/注册页 · auth-gate · 个人资料弹窗 · CSRF 防护 · 所有用户数据表 `user_id` 隔离 |
| **Redis 元数据缓存 + 异步 DB** | ✅ | KV 缓存(Agent/Settings/Workspace/Preference) · Redis Stream write-behind(part.delta 等批量落 PG) · 启动恢复扫描 |
| **记忆管理 UI** | ✅ | 记忆库面板 · 长期记忆/偏好/短期记忆三面板 · 查看/删除/固化 |
| **Agent 可观测性与评测** | ✅ | OpenTelemetry 全链路追踪(Level 4 深度埋点) · Arize Phoenix(:6006) · 在线规则评测(默认开) · 离线 LLM-as-Judge(手动触发) · 5+4 维评测指标体系 |
| 测试覆盖 | 🟡 | 后端 pytest（85+ 测试文件, ruff 全绿）；前端 Vitest 纯函数；E2E 待补 |

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
| REST 客户端 | `src/lib/api.ts` |
| 主题 | `src/components/theme-provider.tsx` · `theme-toggle.tsx` |

### L5 UI 组件（`src/components/`）
| 区域 | 文件 |
|---|---|
| 侧栏（会话/产物库/Agents/知识库/Skills/分析 Tab） | `sidebar.tsx` |
| 聊天主面板 | `chat-panel.tsx` · `message-list.tsx` · `message-item.tsx` · `message-parts.tsx` · `message-highlight-layer.tsx` · `turn-timeline.tsx` |
| 输入框（附件/审批/选区引用/斜杠命令） | `message-input.tsx` · `edit-message-input.tsx` |
| Orchestrator 调度卡 | `dispatch-plan-card.tsx` |
| 产物预览 / 产物库 | `artifact-preview-panel.tsx` · `artifact-library.tsx` · `artifact-code-editor.tsx` |
| 知识库 / 文档 | `knowledge-library.tsx` · `document-detail.tsx` · `document-version-item.tsx` · `upload-document-dialog.tsx` |
| Skills 库 | `skill-library.tsx` |
| 全局搜索 | `global-search.tsx` · `global-search-trigger.tsx` · `search-result-item.tsx` |
| fs_write 审批面板 + diff | `pending-writes-panel.tsx` · `pending-write-diff-tab.tsx` |
| bash 命令审批 | `pending-bash-commands-panel.tsx` |
| ask_user 结构化弹窗 | `ask-user-question-dialog.tsx` |
| Token 计量 | `usage-dashboard.tsx` · `usage-badge.tsx` |
| 文件浏览器 | `file-explorer-panel.tsx` · `file-tab.tsx` · `file-library-dialog.tsx` |
| 选区改写 / 引用 | `selection-popover.tsx` · `quoted-message.tsx` |
| 导航辅助 | `pinned-messages-bar.tsx` · `conversation-outline.tsx` |
| Agent 库 / 创建 | `agent-library.tsx` · `create-agent-dialog.tsx` · `add-agent-dialog.tsx` · `agent-create-wizard.tsx` · `agent-avatar.tsx` · `agent-info-popover.tsx` |
| 会话创建 / 目录选择 | `new-conversation-dialog.tsx` · `dir-picker-dialog.tsx` |
| 设置面板 | `settings-dialog.tsx` |
| 个人资料 | `profile-dialog.tsx` |
| 记忆管理 | `memory-library.tsx` · `memory-management/long-term-memory-panel.tsx` · `memory-management/preference-panel.tsx` · `memory-management/session-memory-panel.tsx` |
| 斜杠命令 | `slash-command-menu.tsx` · `slash-command-help-dialog.tsx` |
| 渲染基建 | `markdown.tsx` · `code-block.tsx` · `attachment-chip.tsx` · `ui/*`（shadcn） |

### L3→L2 API 路由（`backend/app/api/`）
| 端点文件 | 作用 |
|---|---|
| `stream.py` | **SSE 全局事件流**（一条连接） |
| `conversations.py` | 会话 CRUD · 消息发送 · regenerate · compact |
| `messages.py` | 消息操作（edit/pin/bookmark/withdraw） |
| `agents.py` | Agent CRUD（含 Skills 配置） |
| `artifacts.py` | 产物 CRUD · 版本 · 导出 |
| `attachments.py` | 附件上传 |
| `fs.py` | workspace 文件 listdir/read/write |
| `pending.py` | 审批中转（writes/questions/bash/dispatch） |
| `settings.py` | 全局设置 / API key |
| `auth.py` | ★ 用户认证（注册/登录/刷新/登出 · JWT HttpOnly cookie） |
| `runs_misc.py` | run 中止 / usage summary |
| `documents.py` | ★ Document + Version 知识库 CRUD |
| `skills.py` | Skills 上传 / 列表 / 加载 |
| `eval.py` | ★ Agent 评测（`POST /api/eval/judge/{trace_id}` 手动触发 LLM-as-Judge） |
| `deployments.py` | 本地静态发布预览 URL |
| `mobile/routes.py` | 移动端伴随 API（配对 / 远程审批） |

### L3 服务层（`backend/app/services/`）
| 服务 | 文件 | 职责 |
|---|---|---|
| **AgentRunner** | `agent_runner.py` | per-run 生命周期、选 adapter、`build_adapter_input`、历史注入、token 预算 —— **L3 核心** |
| **AgentLoop** | `agent_loop.py` | ★ `run_agent_loop`（solo/coordinated/subagent 三模式统一循环）· `spawn_subagent_loop`（递归子 Agent 派发）· prompt builders |
| DAG 执行器 | `dag_executor.py` | ★ `validate_dag` / `topological_waves` / `execute_dag`（DAG 验证 + 波调度 + 并行执行） |
| Orchestrator stub | `orchestrator.py` · `orchestrator_prompts.py` | 旧三阶段已移除，仅保留 stub 与工具函数 |
| 会话服务 | `conversation_service.py` | 会话/消息持久化 |
| 跨 run 上下文 | `conversation_context.py` | MessagePart → ChatMessage 序列化、pinned 注入 |
| 上下文压缩 | `context_compaction_service.py` | 手动压缩历史为摘要 |
| 事件总线 | `event_bus.py` | asyncio.Queue 扇出，推 SSE |
| 产物服务 | `artifact_service.py` · `deployment_service.py` | 产物 CRUD + 版本链 · 本地静态发布与下载包 |
| Agent / 附件 / 文件 | `agent_runner` 内联 · `attachment_service.py` · `fs_service.py` | |
| 审批中转 store | `pending_writes.py` · `pending_questions.py` · `pending_bash_commands.py` · `pending_dispatch_plans.py` | |
| 设置 / Key | `settings_service.py` · `global_settings_service.py` | 三层 key 优先级解析 · 全局设置缓存（Redis 优先） |
| ★ 异步 DB 写入 | `async_db_writer.py` | Redis Stream 消费者：批量落 PG（part.delta/tool 等事件） |
| ★ 崩溃恢复 | `recovery_scan.py` | 启动时扫描 `status=streaming` 的消息，重放 Stream 或标记 interrupted |
| **★ 可观测性** | `observability/` | OTel 全链路追踪（Level 4 深度埋点）· span 中英文映射 · 在线规则评测 · 离线 LLM-as-Judge · shutdown 清理 |
| 搜索 | `search_service.py` | 消息全文搜索 |
| runner 注册 | `runner_registry.py` | per-conversation runner 生命周期 |
| 部署命令 | `deploy_command_service.py` | 部署斜杠命令 |
| Token 分析 | `usage_summary_service.py` | Token 用量聚合 |
| 网络发现 | `network_hints.py` | 移动端 LAN/Tailscale 发现 |
| **RAG 服务** | `rag_service.py` | ★ 混合检索（Milvus+ES+KG+RRF）+ ingest + delete |
| **Document 服务** | `document_service.py` | ★ Document + Version CRUD + 入库 RAG |
| **PromptAssembler** | `prompt_assembler.py` | ★ 上下文组装（Profile + Recall + Constraints） |
| Skill 服务 | `skill_service.py` | Skills 加载 / 写入 |
| **HookRegistry** | `hook_registry.py` | ★ 生命周期 Hook 注册与分发（10 个事件） |
| 内置 Hooks | `hooks/`（7 个） | audit_log · auto_compact · checkpoint · memory_persist · skill_auto_activator · summary_generate · tool_approval |
| Checkpoint | `checkpoint_service.py` | SDK Agent turn 级检查点保存/恢复 |
| 项目产物 | `project_artifact.py` | 项目级产物管理 |
| Agent 负载 | `agent_load_tracker.py` | Agent 负载追踪 |

### L2 适配器（`backend/app/adapters/`）
| 文件 | 说明 |
|---|---|
| `base.py` | `AdapterInput` + ABC + `AdapterName`（事件流契约，`specs/05`） |
| `registry.py` | adapter 注册/选择（注册 Mock / Custom / ClaudeCLI / CodexCLI） |
| `cli_base.py` | ★ CLI 适配器公共基类：子进程生命周期、stdin/stdout 管道、超时/取消、参数过滤、环境变量合并 |
| `conpty.py` | Windows ConPTY 支持（隐藏子进程窗口、伪终端） |
| `claude_adapter.py` | ★ ClaudeCLIAdapter：`spawn claude` stream-json 协议，CLI 自带工具 |
| `codex_adapter.py` | ★ CodexCLIAdapter：`spawn codex app-server` JSON-RPC 2.0 通信 |
| `custom_adapter.py` | OpenAI 协议 stream + 自驱 tool loop（model-done 主路径，SDK 路线） |
| `custom_provider_client.py` / `session_store.py` | provider 客户端 / 会话存储 |
| `mock_adapter.py` | 假事件流，开发用 |
> MCP Bridge：`backend/app/mcp_bridge.py` — stdio MCP Server，把 `write_artifact`/`ask_user`/`task_dispatch` 等 AChat 平台工具暴露给 CLI agent（Claude/Codex CLI 通过 `--mcp-config` 拉起）。

### 工具系统（`backend/app/tools/`）
`base.py`（ToolContext + ToolDef） · `registry.py`（注册 24 个工具） · `write_artifact.py` · `read_artifact.py` · `deploy_artifact.py` · `deploy_workspace.py` · `read_attachment.py` · `fs_read.py` · `fs_write.py` · `fs_edit.py` · `fs_list.py` · `fs_glob.py` · `fs_grep.py` · `bash.py` · `task_dispatch.py`（子 Agent 派发） · `dispatch_plan.py`（DAG 派发） · `ask_user.py` · `web_search.py` · `memory_rag.py`（memory_recall + rag_search/ingest/list/delete） · `skills.py`（load_skill/write_skill）。详见 `specs/07`。

### RAG 引擎（`backend/app/rag/`）
| 文件 | 职责 |
|---|---|
| `rag_engine.py` | HybridStore：向量(Milvus) + 全文(ES) + 图谱(KG) + RRF 融合 |
| `parser.py` | 文档解析（pdfplumber → PyPDF2 → pdftotext 三级降级） |
| `splitter.py` | 文档分块（chunk_size / overlap 可配） |
| `rewriter.py` | Query Rewriting（LLM 生成扩展查询） |
| `reranker.py` | Reranking（LLM 打分重排） |

### 记忆系统（`backend/app/memory/`）
| 文件 | 职责 |
|---|---|
| `memory_service.py` | ★ 门面：STM + LTM + Preference + GraphMemory |
| `short_term.py` | 短期记忆（chat_history 表，滑动窗口） |
| `long_term.py` | 长期记忆（long_term_memory 表，embedding 语义召回） |
| `preference.py` | 用户偏好（user_preferences 表，KV） |
| `graph_memory.py` | 图谱记忆（Neo4j + memory_nodes/edges 镜像表） |
| `memory_writer.py` | 记忆写入门面 |
| `consolidation.py` | 记忆固化 / 去重 / 衰减 / TTL |

### 知识图谱（`backend/app/graph/`）
| 文件 | 职责 |
|---|---|
| `kgstore.py` | KGStore：文档 → 实体/关系抽取 → Neo4j 入图 → 子图检索 |
| `extractor.py` | LLM 驱动的实体/关系抽取 |
| `types.py` | 图谱类型定义 |

### 基础设施层（`backend/app/infra/`）
| 文件 | 职责 |
|---|---|
| `factory.py` | `build_infrastructure()`：配置驱动，独立降级（Milvus/ES/Neo4j/Kafka/**Redis**） |
| `hybrid.py` | HybridStore 抽象（向量 + 全文 + 图谱统一接口） |
| `cache.py` | ★ Redis KV 元数据缓存（read-through + write-invalidation） |
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
| `models.py` | **22 张表**：14 核心（users/agents/conversations/messages/artifacts/workspaces/attachments/agent_runs/agent_run_checkpoints/context_summaries/app_settings/global_settings/user_settings/mcp_servers）+ 6 AGI-memory（long_term_memory/user_preferences/rag_chunks/chat_history/memory_nodes/memory_edges）+ 2 Document（documents/document_versions） |
| `engine.py` | 异步引擎 + PostgreSQL（连接池） |
| `__init__.py` | 模块导出 |

DB 文件：PostgreSQL（`docker-compose.infra.yml` 启动）；workspace：`.agenthub-data/users/<user_id>/workspaces/<conv_xxx>/`（多用户隔离）。

### 共享类型（`src/shared/`）
`types.ts`（**`StreamEvent` / `MessagePart` 等跨层类型，改动牵一发动全身**） · `constants.ts` · `model-registry.ts` · `ppt-theme.ts` · `codex-compat.ts` · `openai-compatible.ts` 等 15 个文件。前端纯类型，与后端 `backend/app/schemas/` 保持 camelCase 兼容。

### 桌面（`electron/`）& 移动（`apps/mobile/`）
- Electron：`main.ts`（主进程） · `paths.ts`（userData 路径迁移） · `server-bootstrap.ts`。`specs/12`。
- 移动：`apps/mobile/`（Capacitor 伴随客户端，monorepo workspace `@agenthub/mobile`）。`specs/14`。

### 测试
- 后端：`backend/tests/`（pytest，85+ 测试文件，`asyncio_mode = "auto"`，ruff 全绿；含 auth/CSRF/SSE 认证/隔离/异步写入/恢复扫描测试）。
- 前端单元：`src/**/*.test.ts`（Vitest 纯函数）。

---

## 附 · 当前现状（易过时，以 git 为准）

### ✅ 近期完成
- **Agent 可观测性与评测系统**：OpenTelemetry SDK 全链路采集（FastAPI/httpx/openai 自动 instrumentation + Level 4 深度手动埋点 18 处）· OTLP gRPC 发送至 Arize Phoenix（独立 Docker :6006）· 在线规则评测（默认开启，14 指标自动从 trace 计算）· 离线 LLM-as-Judge（默认关闭，手动触发 `POST /api/eval/judge/{trace_id}`）· Agent 全过程评测（5 维度：任务完成/工具调用质量/步骤效率/提示词效果/回答质量）· 多 Agent 协作评测（4 维度：任务拆解/调度效率/子 Agent 质量/聚合质量）· `trace_enabled` 开关（关闭后全 no-op）
- **用户认证与多用户隔离**：JWT(access 1h + refresh 7d) + bcrypt 密码哈希 · 登录/注册页面 · auth-gate 路由保护 · 个人资料弹窗 · CSRF 防护(Origin header) · SSE 连接认证 · 所有用户数据表 `user_id` 隔离 · builtin agent `user_id IS NULL` 共享 · CLI Agent `HOME`/`USERPROFILE` 按用户隔离
- **Redis 元数据缓存 + 异步 DB 写入**：Redis KV 缓存(Agent/UserSettings/Workspace/Preference/GlobalSettings, read-through + write-invalidation) · Redis Stream write-behind(part.delta/tool.call 等事件批量落 PG) · `DBWriterConsumer` 后台消费者 · 启动恢复扫描(streaming 消息重放或标记 interrupted) · 连接池优化(`pool_pre_ping` → `pool_recycle`)
- **记忆管理 UI**：记忆库面板 · 长期记忆/偏好/短期记忆三面板 · 查看/删除/固化操作
- **统一 Agent Loop**（spec 19）：solo / coordinated / subagent 三模式统一为 `run_agent_loop` while-loop，移除旧三阶段 Orchestrator（plan_tasks / report_task_result / verify gate / 重试 harness）
- **通用子任务派发**：任何 Agent 都能通过 `task_dispatch` 克隆自己处理子任务（clone-self，`hidden` 消息），`MAX_DISPATCH_DEPTH=3` 递归深度限制，`dispatch_visibility` 控制消息可见性
- **DAG 派发计划**：`dispatch_plan` 工具声明结构化 DAG，`dag_executor.py` 做拓扑排序 + 波调度 + 并行执行 + 级联跳过，可选计划审批
- **生命周期 Hooks 系统**：7 个内置 Hook（审计/压缩/检查点/记忆/技能/摘要/审批）· 10 个生命周期事件 · Agent 按 `hook_names` 启用
- **Checkpoint 检查点**：SDK Agent turn 级检查点保存与恢复（`agent_run_checkpoints` 表）
- **新增文件工具**：`fs_edit`（文件编辑）· `fs_glob`（glob 搜索）· `fs_grep`（内容搜索）
- **RAG 混合检索系统**：Milvus(向量) + Elasticsearch(全文) + Neo4j(KGStore) 三路召回 + RRF 融合 + Query Rewrite + Rerank
- **分层记忆系统**：STM + LTM(embedding 召回) + Preference + GraphMemory + 自动固化/去重/衰减
- **Document + Version 知识库**：全局文档版本化 · 解析入库 · 按需召回 · 版本刷新三能力
- **PromptAssembler**：Profile + Recall + Constraints 上下文组装，注入 Agent system prompt
- **PostgreSQL 迁移**：从 SQLite 迁移到 PostgreSQL 16（asyncpg），22 张表
- **基础设施层**：Docker Compose 编排（PG/Milvus/ES/Neo4j/Redis），独立降级策略
- **Web 搜索工具**：Tavily API
- **PPT 产物**：ppt 类型 + 真 .pptx 导出 + 完整 theme token

### 🔧 适配器接入路线图

| 适配器 | 路线 | 接入状态 | 说明 |
|---|---|---|---|
| Claude CLI | CLI 子进程 | ✅ 已接入，修 bug 中 | stream-json 协议；MCP bridge 打通平台工具；Windows 环境变量/MCP 工具名前缀已修 |
| Codex CLI | CLI 子进程 | 🔧 代码就绪，验证待完成 | JSON-RPC 2.0；`codex_adapter.py` 已实现，端到端测试与联调待补（tasks T3.2/T4.2） |
| Custom | SDK | ✅ 已实现 | OpenAI 兼容 API + 自驱 tool loop |
| Hermes | CLI 子进程 | ⏳ 待接入 | 规划中 |
| OpenClaw | CLI 子进程 | ⏳ 待接入 | 规划中 |
| OpenCode | CLI 子进程 | ⏳ 待接入 | 规划中 |

> 迁移方案见 `openspec/changes/migrate-claude-codex-to-cli/`。CLI 路线将厂商 CLI 作为子进程拉起，工具执行/沙箱/审批由 CLI 自管，AChat 仅翻译事件流。

### 📋 待办
- OpenSpec 主 specs 同步（orchestrator / tools / stream-events / persistence / core-domain 需更新以反映统一 Agent Loop）
- OpenSpec 主 specs 同步（persistence / platform-security / frontend 需更新以反映用户认证与多用户隔离）
- OpenSpec 主 specs 同步（persistence 需更新以反映 Redis 缓存 + 异步 DB 写入）
- Electron 桌面版改为启动 Python 后端（当前内嵌 Next 已无后端）
- 移动端伴随 App 配对通信打通
- E2E 测试补充（产物预览/导出 + 群聊调度，需测试假 adapter）
- Codex CLI 适配器端到端联调与测试（代码已就绪）
- Hermes / OpenClaw / OpenCode 适配器接入
- 外部 MCP 接入（spec 15）
- RAG 会话级开关细化（`conversations.rag_enabled` 字段已就位）
- 旧编号 specs（01/07/08）逐步迁移到 OpenSpec 体系

### ⚠️ 关键约定（动手前必看）
- 改实体字段 → 同步 `specs/01` + `backend/app/db/models.py`；改事件 → `specs/02` + `backend/app/schemas/events.py` + `src/shared/`；改 Bash 黑名单 → 同步 `specs/11` + `backend/app/utils/` 安全模块。
- 所有 LLM 调用必带取消机制（`asyncio.Event`）；跨进程输入必经 Pydantic 校验；fs/bash 必过 Workspace 沙箱。
- 基础设施客户端**不在 L3 直接 new**，必须经 `backend/app/infra/factory.py`。
- 后端 async 函数调用必须 `await`。
- 完整协作规则见 [CLAUDE.md](./CLAUDE.md)。

---

*最后更新：2026-07-14 · 同步 Agent 可观测性与评测系统（OpenTelemetry + Arize Phoenix + Level 4 深度埋点 + 在线规则评测 + 离线 LLM-as-Judge + 5+4 维评测指标体系）。改动较大后请同步本文件的「功能矩阵」与「当前现状」两节。*
