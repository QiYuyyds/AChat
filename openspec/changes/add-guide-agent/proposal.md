# Proposal: Add Guide Agent

## Why

AChat 的管理操作（建/改 Agent、管 Skill/MCP/知识库、整理记忆、改画像）目前全部是 REST API + 人工点 UI，新用户有认知负担，老用户操作碎片化。引入一个**小A Agent**作为系统"门面引导"，以全局悬浮助手形态常驻，用户用自然语言驱动管理操作，无需手动点 UI。小A 还能 LLM 驱动地智能整理记忆/偏好（剔除垃圾 + 合并重复 + 提炼升华），突破现有算法驱动 `consolidate()` 的 0.95 阈值限制。

## What Changes

- 新增 `is_guide` 标记位到 `Agent` 实体，与 `is_orchestrator` / `is_builtin` 并列；小A Agent 为 builtin + guide，`user_id=NULL` 全局共享
- 后端启动时种子小A Agent（首次在 prod 引入 builtin agent 种子机制）
- `agent_runner` 的 baseline 工具合并跳过 `is_guide=True` 的 Agent
- 新增 7 个管理工具：`manage_agents` / `manage_skills` / `manage_mcp` / `manage_documents` / `manage_memory` / `manage_profile` / `manage_conversations`，仅对 guide Agent 可见，内部复用现有 service 函数，全部经 `ToolContext.user_id` 隔离
- 新增 `manage_memory(action=optimize)` —— LLM 驱动的智能记忆整理（删除 + 合并新建带 embedding + 更新属性），与现有算法驱动 `consolidate()` 互补
- `Conversation.mode` 新增 `'guide'` 值（字符串字段，无 DDL）；guide 会话不出现在 `list_conversations` 结果里、不可被用户删除
- 新增 `guide_side_effect` SSE 事件类型，前端收到后刷新对应面板
- 前端新增 `guideConversationId` store 字段（双活跃会话模型），新增 `GuideFloatingPanel` 悬浮组件（拖拽/缩放/收起展开/快捷键/localStorage 持久化）
- 小A 走 custom adapter SDK 路线 + `run_agent_loop(mode='solo')`，无新 adapter、无独立服务路径
- 小A 开箱即用：`api_key=NULL`，走 `get_effective_api_key` 的环境变量兜底层（`DEEPSEEK_API_KEY`），用户无需填写任何 Key

## Capabilities

### New Capabilities

- `guide-agent`: 小A Agent 全局悬浮助手——管理引导 Agent 的行为契约（管理边界、确认规则、记忆整理规则）、7 个管理工具规格、悬浮面板 UX、双活跃会话模型、guide 会话生命周期

### Modified Capabilities

- `core-domain`: `Agent` 实体新增 `is_guide` 布尔字段；`Conversation.mode` 新增 `'guide'` 值
- `adapters`: guide agent（`is_guide=True`）跳过 baseline 工具合并
- `tools`: 新增 7 个管理工具（`manage_agents` / `manage_skills` / `manage_mcp` / `manage_documents` / `manage_memory` / `manage_profile` / `manage_conversations`），仅对 guide agent 注入
- `stream-events`: 新增 `guide_side_effect` 事件类型
- `persistence`: `agents` 表加 `is_guide` 列（幂等 ALTER）；`conversations` 表 `mode` 字段支持 `'guide'` 值（无 DDL）；Agent 缓存层加 `is_guide`
- `frontend`: AppState 新增 `guideConversationId` + `guidePanelState`；新增 `GuideFloatingPanel` 组件；`list_conversations` 过滤 guide 会话

## Impact

**后端**：
- `backend/app/db/models.py` — Agent 类加 `is_guide` 字段
- `backend/app/db/engine.py` — `_migrate_columns` 加幂等 ALTER
- `backend/app/main.py` — lifespan startup 加 `_seed_guide_agent`
- `backend/app/services/agent_runner.py` — baseline 合并加 `and not agent.is_guide` 条件；guide agent 工具注入逻辑
- `backend/app/tools/registry.py` — 注册 7 个管理工具
- `backend/app/tools/manage_agents.py` / `manage_skills.py` / `manage_mcp.py` / `manage_documents.py` / `manage_memory.py` / `manage_profile.py` / `manage_conversations.py` — 新文件
- `backend/app/services/conversation_service.py` — `create_conversation` 支持 `mode='guide'`；`list_conversations` 过滤 `mode != 'guide'`；`delete_conversation` 拒绝 guide 会话
- `backend/app/schemas/events.py` — 新增 `GuideSideEffectEvent`，加入 `StreamEvent` union
- `backend/app/infra/cache_helpers.py` — Agent 缓存字段列表加 `is_guide`
- `backend/app/api/agents.py` — `_serialize` 加 `isGuide` 字段
- `backend/.env.example` — 文档化 `DEEPSEEK_API_KEY`（小A 默认 provider）

**前端**：
- `src/stores/app-store.ts` — 新增 `guideConversationId` + `guidePanelState` 字段及对应 setter
- `src/components/guide-floating-panel.tsx` — 新组件（拖拽/缩放/收起展开/快捷键/精简 MessageList + MessageInput/ask_user 内联渲染）
- `src/shared/types.ts` — 新增 `GuideSideEffectEvent` 类型
- `src/lib/api.ts` — `createConversation` 支持 `mode='guide'`
- `src/app/.../layout` 或 `Home` 组件 — 挂载 `GuideFloatingPanel`
- 各面板组件 — 监听 `guide_side_effect` 事件并刷新

**测试**：
- `backend/tests/test_guide_agent_seed.py` — 种子机制
- `backend/tests/test_manage_tools.py` — 7 个管理工具
- `backend/tests/test_guide_conversation.py` — guide 会话创建/列表过滤/删除拒绝
- `backend/tests/test_guide_side_effect_event.py` — 事件

**Spec 文档**：
- `specs/01-core-entities.md` — Agent 字段表加 `is_guide`，Conversation mode 加 `'guide'`
- `specs/02-stream-events.md` — 加 `guide_side_effect` 事件
- `specs/07-tools.md` — 加 7 个管理工具
- `specs/05-adapter-interface.md` — baseline 跳过 guide agent
- `specs/08-db-schema.md` — agents 表加 `is_guide` 列
- `specs/09-frontend-architecture.md` — 双活跃会话 + 悬浮面板

**无破坏性变更**：所有改动都是加法性的。`is_guide` 默认 `False`，现有 Agent 不受影响；`mode='guide'` 是新值，现有会话不受影响；管理工具仅对 guide agent 注入，普通 agent 即使误配也 block。
