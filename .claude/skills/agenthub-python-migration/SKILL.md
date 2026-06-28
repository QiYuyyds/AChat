---
name: agenthub-python-migration
description: AChat TypeScript 后端迁移到 Python FastAPI 的完整指南
---

# AChat Python 后端迁移指南

## 项目概述

AChat 是一个 local-first 多 Agent 协作工作空间，把 AI 协作做成 IM 群聊式的体验。当前正在将 TypeScript (Next.js) 后端迁移到 Python (FastAPI)。

## 原项目架构

### 五层架构

```
L5 UI (React/shadcn)           → 保持不变
L4 State + Transport (Zustand) → 保持不变，连接新后端
L3 Application Services        → Python 重写
L2 Agent Platform Adapters     → Python 重写
L1 Persistence (SQLite)        → SQLAlchemy 重写
```

### 原 TypeScript 关键文件

| 模块 | 路径 | 说明 |
|------|------|------|
| **数据库 Schema** | `src/db/schema.ts` | 9 张表定义 (Drizzle) |
| **共享类型** | `src/shared/types.ts` | StreamEvent, MessagePart 等 |
| **Agent Runner** | `src/server/agent-runner.ts` | 核心执行器 (~2,746 行) |
| **会话服务** | `src/server/conversation-service.ts` | 会话/消息管理 (~1,021 行) |
| **事件总线** | `src/server/event-bus.ts` | SSE 事件分发 |
| **适配器** | `src/server/adapters/*.ts` | Claude/Codex/Custom/Mock |
| **工具** | `src/server/tools/*.ts` | 12 个内置工具 |
| **API 路由** | `src/app/api/**/*.ts` | 50+ API 端点 |

## 数据库模型 (9 张表)

| 表名 | 说明 | 关键字段 |
|------|------|----------|
| `agents` | AI 代理 | id, name, adapter_name, system_prompt, tool_names |
| `conversations` | 会话 | id, mode(single/group), agent_ids, pinned_message_ids |
| `messages` | 消息 | id, role, parts(JSON), status, run_id |
| `artifacts` | 产物 | id, type, content(JSON), version, parent_artifact_id |
| `workspaces` | 工作区 | id, mode(sandbox/local), root_path, bound_path |
| `attachments` | 附件 | id, kind(image/file), file_path, mime_type |
| `agent_runs` | 运行记录 | id, status, usage(JSON), parent_run_id |
| `context_summaries` | 上下文压缩 | id, summary, covered_until_message_id |
| `app_settings` | 全局设置 | 单行表，存 API keys |

## StreamEvent 类型 (30+ 事件)

核心事件流契约，前后端通过 SSE 传输：

```
Run 生命周期: run.start, run.end, run.usage
Message: message.start, message.end, message.added, message.removed
Part 增量: part.start, part.delta, part.end
Tool: tool.call, tool.result
Artifact: artifact.create, artifact.update
Dispatch: dispatch.plan, dispatch.start, dispatch.end
审批: fs_write.pending, bash_command.pending, ask_user.pending
心跳: heartbeat
```

## API 路由清单

### 核心路由
- `GET/POST /api/conversations` - 会话列表/创建
- `GET/PATCH/DELETE /api/conversations/{id}` - 会话操作
- `GET/POST /api/conversations/{id}/messages` - 消息
- `GET /api/stream` - SSE 事件流

### 消息操作
- `POST /api/messages/{id}/edit` - 编辑重发
- `POST /api/messages/{id}/withdraw` - 撤回
- `POST /api/messages/{id}/pin` - Pin 到 LLM 上下文
- `POST /api/messages/{id}/bookmark` - UI 书签

### Agent 管理
- `GET/POST /api/agents` - 列表/创建
- `PATCH/DELETE /api/agents/{id}` - 修改/删除

### 审批流程
- `GET/POST /api/conversations/{id}/pending-writes/{pwId}`
- `GET/POST /api/conversations/{id}/pending-questions/{qid}`
- `GET/POST /api/conversations/{id}/pending-bash-commands/{id}`

## Agent 适配器

| 适配器 | 说明 | Python 实现 |
|--------|------|-------------|
| `MockAdapter` | 开发用，不消耗 token | 简单流式脚本 |
| `CustomAdapter` | OpenAI 兼容 (DeepSeek/火山方舟等) | openai SDK + tool loop |
| `ClaudeAdapter` | Anthropic Claude | anthropic SDK + Messages API |
| `CodexAdapter` | OpenAI Codex | 暂不实现 |

### 适配器核心流程

```python
async def stream(self, input: AdapterInput, cancel: Event) -> AsyncIterator[StreamEvent]:
    # 1. 构造请求
    # 2. 流式调用 LLM
    # 3. 解析 tool_calls
    # 4. 执行工具 (MAX_TURNS=8)
    # 5. yield StreamEvent
```

## 工具系统 (12 个内置工具)

| 工具名 | 功能 |
|--------|------|
| `write_artifact` | 创建产物 (web_app/document/image/ppt) |
| `read_artifact` | 读取产物 |
| `deploy_artifact` | 部署产物预览 |
| `fs_read` / `fs_write` / `fs_list` | 文件系统操作 |
| `bash` | 执行命令 (带黑名单+审批) |
| `ask_user` | 结构化问用户 |
| `plan_tasks` | Orchestrator 规划 |
| `report_task_result` | 子任务上报 |

## Orchestrator 三阶段流程

```
Stage 1: PLAN
  └─ Orchestrator 生成计划 (plan_tasks 工具调用)

Stage 2: EXECUTE (最多 4 轮)
  ├─ 人工审批/修改计划
  ├─ DAG 调度 (最多 4 并发)
  ├─ 子任务执行 (最多 4 次重试)
  └─ 代码冲突检测

Stage 3: AGGREGATE
  └─ Orchestrator 总结
```

## Python 后端结构

```
backend/
├── pyproject.toml
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置
│   ├── db/
│   │   ├── engine.py        # SQLAlchemy 引擎
│   │   └── models.py        # 9 张 ORM 模型
│   ├── schemas/             # Pydantic 类型
│   │   ├── events.py        # StreamEvent
│   │   ├── messages.py      # MessagePart
│   │   ├── artifacts.py     # ArtifactContent
│   │   ├── dispatch.py      # DispatchPlanItem
│   │   └── requests.py      # API 请求/响应
│   ├── services/
│   │   ├── event_bus.py     # 事件总线
│   │   ├── conversation_service.py
│   │   ├── artifact_service.py
│   │   ├── agent_runner.py  # 核心
│   │   └── settings_service.py
│   ├── adapters/
│   │   ├── base.py          # ABC
│   │   ├── mock_adapter.py
│   │   ├── custom_adapter.py
│   │   └── claude_adapter.py
│   ├── tools/
│   │   ├── base.py
│   │   ├── registry.py
│   │   └── *.py             # 各工具实现
│   └── api/                 # FastAPI 路由
```

## 关键技术映射

| TypeScript | Python |
|------------|--------|
| Drizzle ORM | SQLAlchemy 2.0 |
| Zod | Pydantic v2 |
| AsyncIterable | async generators |
| AbortSignal | asyncio.Event |
| EventEmitter | asyncio.Queue + subscribers |
| @anthropic-ai/claude-agent-sdk | anthropic SDK |
| openai (JS) | openai (Python) |

## 当前进度

### ✅ 已完成
- 阶段 1: 项目初始化与数据库层
  - 项目骨架 (pyproject.toml, requirements.txt)
  - 数据库模型 (9 张表)
  - Pydantic schemas (events, messages, artifacts, dispatch, requests)
  - API 路由占位
- 阶段 2: 核心服务层
  - `services/event_bus.py` — asyncio.Queue 订阅者模式的事件总线 (替代 EventEmitter)
  - `services/conversation_service.py` — 会话 CRUD + 消息全生命周期 (send/withdraw/edit/regenerate/pin/bookmark/clear/delete/revise-plan)
  - `services/pending_dispatch_plans.py` — 待审计划内存存储
  - `services/deploy_command_service.py` — /deploy 命令解析与处理 (实际部署工具走注册表，待阶段3)
  - `services/runner_registry.py` — AgentRunner 延迟绑定 (破前向依赖，待阶段5)
  - 辅助: `utils/platform.py`、`utils/clock.py`、`utils/workspace_utils.py`、`adapters/session_store.py`
  - 修复: `db/engine.py` 开启 SQLite `PRAGMA foreign_keys=ON`，使级联删除生效
  - 测试: `tests/test_event_bus.py` + `tests/test_conversation_service.py` (26 passed)
- 阶段 3: 工具系统
  - `tools/base.py` + `tools/registry.py` (ToolContext 用 `asyncio.Event` 取消信号；registry build 时 `set_deploy_handlers` 接入 deploy 工具)
  - 12 个工具: write_artifact / read_artifact / fs_read / fs_write / fs_list / bash / ask_user / plan_tasks / report_task_result / deploy_artifact / deploy_workspace / read_attachment
  - 支撑模块: `utils/security.py` (双平台黑名单)、`utils/mermaid_normalize.py`、`utils/ppt_normalize.py`、`utils/dispatch_run_evidence.py`、`utils/dispatch_file_writes.py`、`utils/artifact_preview.py`、`utils/approval.py`
  - 支撑服务: `services/artifact_service.py` (build_artifact_content)、`fs_service.py`、`pending_writes/questions/bash_commands.py`、`bash_command_approval.py`、`task_result_report.py`、`settings_service.py` (只读)、`attachment_service.py` (只读)、`deployment_service.py` (创建+发布)
  - 新增依赖: `pypdf` (read_attachment PDF 抽取，替代 TS pdf-parse)
  - 测试: `tests/test_artifact_content.py` + `tests/test_tools.py` (新增 25，全套 51 passed)，ruff 干净
  - 延后: report_task_result 完成度评估 (阶段5)；产物CRUD/settings UPSERT/attachment写/deployment asset服务 (阶段6)
- 阶段 4: Agent 适配器 (workflow 编排: Foundation→Port×3→Integrate→Review×3)
  - `adapters/base.py` (AdapterInput/AdapterAttachment/CustomConfig + ABC, AbortSignal→asyncio.Event)
  - `mock_adapter.py` (脚本流) / `custom_adapter.py` (AsyncOpenAI 工具循环 MAX_TURNS=8 + reasoning + usage + 多模态) / `claude_adapter.py` (AsyncAnthropic Messages API + 工具循环, name='claude-code')
  - `custom_provider_client.py` (provider 配置+校验)、`registry.py` (mock/custom/claude；Codex 暂不实现)、`utils/ids.new_tool_call_id`
  - 关键决策: Claude 走 Messages API 自实现工具循环(非 CLI SDK 移植); SDK client 经 `_build_client` 便于 mock
  - 启用依赖: openai / anthropic SDK
  - 测试: 3 个适配器测试 (全套 64 passed)，ruff 干净；对抗审查 3/3 faithful

- 阶段 5: AgentRunner 核心 (workflow 编排: Understand×5→Support×5→Core-A→Core-B→Integrate+Review×2，15 agents)
  - `services/agent_runner.py` (1365 行) — simple 路径 + 共享机制 (`AgentRunnerImpl` run/abort、execute_run 懒加载 orchestrator 破循环、consume_stream、persist_event、finalize 幂等、build_adapter_input、_Semaphore、run_with_args)；模块加载即 `runner_registry.set_agent_runner`
  - `services/orchestrator.py` (1369 行) — 三阶段 PLAN→EXECUTE→AGGREGATE + replan、DAG 调度 + 并发、子任务重试、证据门禁、冲突检测
  - `services/orchestrator_prompts.py` (571 行) — prompt/XML 构建器逐字移植
  - Support 依赖: `utils/model_registry.py`、`services/dispatch_plan.py` (834)、`project_artifact.py`、`conversation_context.py` + `context_compaction_service.py`、`task_result_report.evaluate` 完成度门禁
  - seam 解耦: orchestrator import agent_runner 的 RunArgs/execute_run/run_with_args/consume_stream/_Semaphore；agent_runner 懒加载 orchestrator.execute_orchestrator_run
  - 测试: 阶段 5 新增 87，**全套 168 passed**；阶段 5 文件 ruff 干净
  - 对抗审查修复: `_normalize_path` 的 `.lstrip("./")` 误吃 dotfile → 改 `re.sub(r"^\./+", ...)`
  - 遗留: `app/api/*` 占位路由 + 阶段 1-2 旧文件 48 处 ruff 告警，随阶段 6 一并清理

- 阶段 6: API 路由 (workflow 编排: Understand×4→Scaffold→Services×4→Routers pipeline×10→Integrate→Review×2，22 agents)
  - 51 个 TS 路由 → 10 个 router 文件全部实现并接入 main.py：conversations/messages/agents/artifacts/attachments/fs/pending/settings/runs_misc/mobile.routes
  - 补齐延后 service 写方法: artifact CRUD+版本链+export、settings UPSERT+mobile-token、attachment upload/delete、deployment asset+zip
  - Scaffold 独占: 补 Pydantic schema + conftest httpx `api_client` ASGI 测试客户端 fixture
  - Integrate: 删除被 runs_misc 取代的 runs.py/search.py；pending include 不加 prefix (装饰器自带 /api)
  - 路由层极薄: 直接调 service (自管 get_db)，仅 error→HTTP status 翻译；线格式 camelCase via model_dump(by_alias=True)
  - 测试: 每组独立 test_api_*.py 用 api_client fixture；**全套 386 passed**；阶段 6 文件 ruff 干净
  - 对抗审查 2/2 faithful；修 1 MAJOR (mobile deploy_status optional 字段 null→按 None 过滤键对齐 JS undefined-drop)
  - 延后: compact 真实压缩 (依赖未移植的 context_compaction.compact_conversation)；SSE→阶段7；偶发 SQLite locked flake→阶段9
  - 防冲突: 共享文件单一所有权 (Scaffold 独占 schemas/conftest、Integrate 独占 main.py、router/service 一人一文件) → 22 agents 零写冲突

- 阶段 7: SSE 实时推送 (直接实现)
  - 重写 `app/api/stream.py`: 接真 `event_bus.subscribe()` (替换占位的自建 _subscribers)；纯 `data:` 帧 (移除错误的 SSE `event:` 字段)；首帧 `{type:'connected'}`；15s 空闲心跳；`model_dump_json(by_alias=True)` 保 camelCase 上线
  - 测试 `tests/test_api_stream.py` (4): connected 首帧 / 事件转发 camelCase / 空闲心跳 / 关闭退订；**全套 390 passed**
- 阶段 8: 前端适配 (直接实现)
  - 新建 `src/lib/config.ts` 导出 `API_BASE_URL` (env NEXT_PUBLIC_API_BASE_URL，默认空串=同源零回归)
  - `src/lib/api.ts` 全部 REST fetch 加前缀；`src/components/stream-provider.tsx` EventSource 加前缀；`.env.example` 加示例
  - 后端 CORS 已就位 (config 默认 localhost:3000)；安装 uvicorn[standard]
  - 连通性实测 (uvicorn :8123): /health 200、/api/conversations 200、CORS 预检放行 localhost:3000、SSE 首帧 connected + text/event-stream 头
  - 延后: 前端完整 typecheck/浏览器 E2E 需先 pnpm install → 阶段 9

- 阶段 9: 集成测试与优化 (直接实现，后端完成)
  - 修 SQLite locked flake: engine.py 加 PRAGMA journal_mode=WAL + busy_timeout=5000；连跑 3 次全套 390 passed (提速 ~36s)
  - ruff 全清: 16 早期告警 → 0 (events 判别联合 noqa、event_bus contextlib.suppress、workspace_utils SYSTEMDRIVE+all())
  - 依赖齐全 (uvicorn/httpx/sse-starlette 已记入 pyproject+requirements)
  - 交接本地: pnpm install + typecheck + 浏览器 E2E (两终端: uvicorn :8000 / NEXT_PUBLIC_API_BASE_URL 起前端)
  - 仍延后: compact 真实压缩 (依赖未移植的 LLM 摘要流)、Codex 适配器、Claude thinking、外部 MCP

### ✅ 迁移完成 (后端)
后端全栈移植完成: 9 表 + 30+ 事件 + 12 工具 + 3 适配器 + AgentRunner(simple+orchestrator) + 51 路由 + SSE，**390 tests passed, ruff 全绿**。前端切换可配 API_BASE_URL，后端连通性 socket 实测通过。剩前端 typecheck/浏览器 E2E 待本地验证。

### 🚧 待完成 (本地验证)
- 阶段 8: 前端适配
- 阶段 9: 集成测试

## 使用说明

### 启动开发服务器

```bash
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

### 连接现有数据库

配置 `.env`:
```
DATABASE_URL=sqlite+aiosqlite:///../.agenthub-data/agenthub.db
```

## 参考文档

- 计划文档: `~/.claude/plans/keen-jingling-oasis.md`
- 原项目 OVERVIEW: `OVERVIEW.md`
- 原项目 CLAUDE.md: `CLAUDE.md`
- 规格文档: `specs/` 目录
