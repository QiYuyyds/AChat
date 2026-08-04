# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

个人独立开发者。典型场景：开发者坐在电脑前，同时管理多个 AI Agent 对话、workspace 文件和产物审批。当前不支持多用户在同一对话中协作；多用户隔离是为未来预留的。

## Product Purpose

AChat 把多 Agent 协作做成 IM 群聊体验——Agent 是「联系人」，对话是「工作空间」，Orchestrator 是「群里的项目经理」。

成功意味着：一个开发者能在一次工作会话中高效编排 3+ 个 Agent 并行完成任务，且市面上的各种 Agent（Claude Code、Codex CLI、Pi Agent、Hermes Agent 等）都能接入并统一管理。

## Positioning

IM 范式的多 Agent 工作空间。与终端中孤立运行 Agent、或单纯与单个 LLM 聊天不同，AChat 将多 Agent 协作作为一等公民：结构化消息部件（非 markdown 整块）、独立产物生命周期、workspace 沙箱与审批门、跨会话记忆与 RAG 知识库、DAG 编排的并行任务派发。

## Operating Context

本地优先的开发工具。开发者在自己的机器上运行 AChat（前端 Next.js :3000 + 后端 Python FastAPI :8000），基础设施（PostgreSQL / Milvus / ES / Neo4j）通过 Docker Compose 启动，各服务独立降级。

- 双 DB 架构：本地 SQLite[WAL] 承载对话热数据 + 远端 PostgreSQL 承载用户系统与知识/RAG 数据
- 每个会话有独立 workspace（sandbox 模式有配额限制 / local 模式绑定真实项目目录）
- Agent 执行留在用户机器上；CLI Agent（Claude Code / Codex）以子进程方式拉起，工具/沙箱/审批由 CLI 自管
- Electron 桌面打包与 Web 版同等重要，共享同一套 React/Next.js 代码库与设计语言
- 移动伴随端（Capacitor）设计为通过 LAN 或 Tailscale 连接桌面端，用于观察运行、发消息和处理审批

## Capabilities and Constraints

已确认功能：

- IM 会话管理：单聊 / 群聊（@提及）/ 多会话并行 / 搜索 / 置顶 / 归档 / 书签 / 引用回复 / 编辑重发 / 撤回 / 重新生成
- 统一适配器层：Claude Code（CLI 子进程 stream-json）/ Codex（CLI 子进程 JSON-RPC 2.0）/ Custom（OpenAI 兼容 SDK + 自驱 tool loop）/ Mock（开发用）
- 统一 Agent Loop：solo / coordinated / subagent 三模式共用 `run_agent_loop`；任何 Agent 可通过 `task_dispatch` 克隆自己处理子任务（递归深度上限 3）
- Orchestrator DAG 调度：拓扑排序 + 波调度并行执行 + 级联跳过 + 可选审批
- 产物系统：web_app / document / image / ppt / code_file / diff，独立生命周期与版本链，内联预览与编辑
- Workspace 沙箱：sandbox 模式（100MB / 1000 文件配额）/ local 模式（真实项目目录）；fs 工具与 bash 强制路径解析在生效 workspace 子树内
- 双平台 Bash 黑名单（POSIX / Windows 各一套）
- RAG 混合检索：Milvus 向量 + ES 全文 + Neo4j 图谱 + RRF 融合 + Query Rewrite + Rerank
- 分层记忆系统：STM + LTM（embedding 召回）+ SessionMemory + Preference + GraphMemory + 自动固化/衰减
- Document + Version 知识库：全局文档版本化、解析入库、按需召回、版本刷新
- Obsidian vault 同步与预处理入库
- 外部 MCP 接入：Server 配置管理、client_manager、调用审批；MCP bridge 将平台工具暴露给 CLI agent
- Run 内压缩：五阶段递进压缩 pipeline（纯结构化裁剪，无 LLM）
- Worktree 隔离：DAG 并行任务用 git worktree 隔离 + 自动 merge-back + 三层递进冲突解决
- 用户认证与多用户隔离：JWT + bcrypt，所有用户数据表 `user_id` 隔离
- Agent 可观测性：OpenTelemetry 全链路追踪 + Arize Phoenix + 在线规则评测 + 离线 LLM-as-Judge
- Guide Agent「小A」：builtin 全局悬浮助手，7 个管理工具，双活跃会话模型，开箱即用
- ModelProfile：用户级模型配置，独立于 Agent 实体，运行时解析
- 代码图谱智能：CodeGraph 本地运行时 + `code_explore` 工具
- 执行计划工具：`create_plan` / `plan_step` / `add_plan_steps` 结构化计划卡片

约束：

- 本地优先：Agent 执行与数据留在用户机器上，无托管 key 服务
- LLM 输出永远不可信：iframe `sandbox="allow-scripts"`、bash 白名单/黑名单、参数化 SQL
- CLI Agent（Claude Code / Codex）的 `HOME` / `USERPROFILE` 按用户隔离
- 基础设施客户端不在 L3 直接 new，必须经 `infra/factory.py` 统一构建
- 所有 LLM 调用必须支持取消（后端 `asyncio.Event`）
- 后端 async 函数调用必须 `await`

待决事实：

- Electron 桌面版需改为启动独立 Python 后端进程（当前内嵌 Next 已无后端 API 路由）
- 移动伴随端配对通信待打通
- Codex CLI 适配器端到端联调与测试待补
- Hermes / Pi Agent / OpenCode 等适配器待接入

## Brand Commitments

- 产品名：AChat
- 内置 Guide Agent 名称：「小A」
- License：AGPL-3.0-only
- Agent 图标库：22 个表情角色图标（`Agent Icon Library/` + `public/agent-icons/`）

## Evidence on Hand

- `docs/AChat封面.gif` — 产品封面动画
- `docs/功能演示.gif` — 功能演示动画
- `Agent Icon Library/` — 22 个 Agent 表情角色 PNG 图标
- `login-design-previews/` — 登录页设计预览
- `backend/tests/` — 141 个后端测试文件
- `eval/` — RAG 评测语料与工具
- 无虚构的客户证言、基准测试或部署声明

## Product Principles

1. **IM 范式优先**：多 Agent 协作应该像 IM 群聊，不像终端会话——结构化消息部件、独立产物生命周期、流式事件协议
2. **本地优先与隐私**：Agent 执行和数据留在用户机器上，无托管 key 服务，基础设施可独立降级
3. **通用 Agent 接入**：市面上的各种 Agent 都应可通过 CLI 子进程或 SDK 适配器接入并统一管理
4. **结构化而非整块**：消息是 parts 数组而非 markdown 字符串，产物有独立生命周期，事件有类型契约
5. **优雅降级**：基础设施服务（Milvus / ES / Neo4j / Phoenix）各自独立降级，不阻断核心对话流
