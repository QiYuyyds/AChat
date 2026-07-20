## 1. 后端基础设施（M1：is_guide 字段 + 种子 + baseline 跳过）

- [x] 1.1 在 `backend/app/db/models.py` 的 `Agent` 类新增 `is_guide: Mapped[bool]` 字段（与 `is_orchestrator` 并列，`nullable=False, default=False`）
- [x] 1.2 在 `backend/app/db/engine.py` 的 `_migrate_columns` statements 列表新增 `"ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_guide BOOLEAN NOT NULL DEFAULT FALSE"`
- [x] 1.3 在 `backend/app/infra/cache_helpers.py` 的 Agent 缓存字段列表新增 `"is_guide"`
- [x] 1.4 在 `backend/app/api/agents.py` 的 `_serialize` 函数返回字典新增 `"isGuide": row.is_guide`
- [x] 1.5 在 `backend/app/main.py` 的 lifespan startup 新增 `_seed_guide_agent(db)` 函数：检查 `is_guide=True` 的 Agent 是否存在，不存在则创建 `ag_guide_builtin`（name=小A, avatar=🅰️, adapter_name=custom, model_provider=deepseek, model_id=deepseek-v4-flash, api_key=NULL, tool_names=7个管理工具, is_builtin=True, is_guide=True, user_id=None, system_prompt=小A prompt）
- [x] 1.6 定义小A system prompt 常量（管理边界、确认规则、记忆整理规则、交互风格、活动回顾规则），存于 `backend/app/services/guide_prompt.py` 或 `main.py` 内
- [x] 1.7 在 `backend/app/services/agent_runner.py` 的 baseline 合并逻辑（L1847 附近）加 `and not agent.is_guide` 条件，使 guide agent 跳过 baseline 合并
- [x] 1.8 在 `backend/.env.example` 文档化 `DEEPSEEK_API_KEY`（小A 默认 provider 的环境变量兜底）
- [x] 1.9 编写 `backend/tests/test_guide_agent_seed.py`：测试种子幂等性（首次创建、重启不重复）、种子失败不阻断启动

## 2. 管理工具集（M2：7 个管理工具 + 注册 + 注入）

- [x] 2.1 创建 `backend/app/tools/manage_agents.py`：实现 `manage_agents` 工具（action: list/create/update/delete），内部复用 `api/agents.py` 的 `_serialize` / `_create_custom_agent` / `_update_agent` / `_delete_custom_agent`，delete 检查 `confirm=true` 且非 builtin
- [x] 2.2 创建 `backend/app/tools/manage_skills.py`：实现 `manage_skills` 工具（action: list/create/delete），复用 `skill_service`，delete 检查 `confirm`
- [x] 2.3 创建 `backend/app/tools/manage_mcp.py`：实现 `manage_mcp` 工具（action: list/create/update/delete），复用 `mcp/client_manager.py` + `api/mcp.py` CRUD，delete 检查 `confirm`
- [x] 2.4 创建 `backend/app/tools/manage_documents.py`：实现 `manage_documents` 工具（action: list/upload/delete/refresh），复用 `document_service.py`，delete 检查 `confirm`
- [x] 2.5 创建 `backend/app/tools/manage_memory.py`：实现 `manage_memory` 工具（action: list/delete/consolidate/optimize），list/delete/consolidate 复用 `memory_service` + `long_term.py` + `preference.py`，optimize 执行 plan（delete_ids + merge_groups 新建带 embedding + update_ids），delete 检查 `confirm`
- [x] 2.6 创建 `backend/app/tools/manage_profile.py`：实现 `manage_profile` 工具（action: get/update），复用 `api/profile.py` + `settings_service.py`，修改 API Key 检查 `confirm`
- [x] 2.7 创建 `backend/app/tools/manage_conversations.py`：实现 `manage_conversations` 工具（action: list/get/search/update/delete），复用 `conversation_service` + `search_service.search_messages`，list 支持 `since_hours` / `include_archived` 筛选，delete 检查 `confirm` 且拒绝 guide 会话
- [x] 2.8 在 `backend/app/tools/registry.py` 的 `_build_registry` 注册 7 个管理工具
- [x] 2.9 在 `backend/app/services/agent_runner.py` 的工具注入逻辑加 guide-agent 过滤：guide agent 只注入管理工具 + `ask_user`；非 guide agent 过滤掉管理工具（即使 `tool_names` 误配）
- [x] 2.10 管理工具执行成功后，通过 EventBus 发送 `guide_side_effect` 事件（target + action + conversationId + user_id）
- [x] 2.11 编写 `backend/tests/test_manage_tools.py`：测试 7 个工具的 list/create/update/delete/confirm 逻辑、user_id 隔离、builtin 保护

## 3. 会话层支持（M3：mode='guide' + list 过滤 + 事件类型）

- [x] 3.1 在 `backend/app/services/conversation_service.py` 的 `create_conversation` 支持 `mode='guide'`：跳过 agent 数量校验，创建空 sandbox workspace，`agentIds=['ag_guide_builtin']`
- [x] 3.2 在 `list_conversations` 查询加 `WHERE mode != 'guide'` 过滤（或等价的 Python 层过滤）
- [x] 3.3 在 `delete_conversation` 加检查：`mode='guide'` 的会话拒绝删除
- [x] 3.4 在 `backend/app/schemas/events.py` 新增 `GuideSideEffectEvent` 类（type='guide_side_effect', conversationId, target, action, payload?），加入 `StreamEvent` union
- [x] 3.5 在 `backend/app/schemas/events.py` 确认 `GuideSideEffectEvent` 继承 `BaseEvent`（携带 user_id 用于 EventBus 过滤）
- [x] 3.6 编写 `backend/tests/test_guide_conversation.py`：测试 guide 会话创建、list 过滤、delete 拒绝
- [x] 3.7 编写 `backend/tests/test_guide_conversation.py`：测试事件 schema + EventBus user_id 过滤（合并入 3.6 文件）

## 4. 前端悬浮面板（M4：store + 组件 + 交互）

- [x] 4.1 在 `src/stores/app-store.ts` 的 `AppState` 新增 `guideConversationId: string | null` 和 `guidePanelState: { open: boolean; position: {x,y}; size: {width,height} }`，及对应 setter
- [x] 4.2 在 `src/shared/types.ts` 新增 `GuideSideEffectEvent` 类型定义（与后端 schema camelCase 兼容）
- [x] 4.3 在 `src/lib/api.ts` 的 `createConversation` 支持 `mode='guide'` 参数
- [x] 4.4 创建 `src/components/guide-floating-panel.tsx`：悬浮面板主组件（header 拖拽 + 右下角缩放手柄 + 收起/展开按钮 + 连接状态指示）
- [x] 4.5 实现面板拖拽逻辑（header mousedown → mousemove 更新 position → mouseup，存 localStorage）
- [x] 4.6 实现面板缩放逻辑（右下角手柄拖拽，限制范围 320×400 ~ 600×800，存 localStorage）
- [x] 4.7 实现收起/展开逻辑（点 ✕ 或 `Ctrl/Cmd+G` 切换，收起时显示悬浮按钮带未读红点，存 localStorage）
- [x] 4.8 实现精简 MessageList：读 `guideConversationId` 的消息，渲染 text part（markdown）、tool_use part（折叠卡片）、ask_user part（内联选项按钮），不渲染其他 part 类型
- [x] 4.9 实现精简 MessageInput：无附件、无斜杠命令、无 @mention，`mentionedAgentIds` 固定为 `['ag_guide_builtin']`，调用 `POST /api/conversations/{guideConvId}/messages`
- [x] 4.10 实现 ask_user 内联渲染：读 `pendingQuestionsByConv[guideConvId]`，在对应 assistant 消息下方渲染选项按钮，点击 → `POST /api/pending/questions/{id}/resolve`，点击后禁用
- [x] 4.11 实现首次登录自动创建 guide 会话：`useEffect` 检查 `guideConversationId` 为空时调用 `createConversation({mode:'guide', agentIds:['ag_guide_builtin']})`，展开面板
- [x] 4.12 实现移动端响应式：屏幕宽度 < 768px 时面板全屏覆盖
- [x] 4.13 在 `Home` 组件或根 layout 挂载 `<GuideFloatingPanel />`

## 5. 副作用通知（M5：事件 reducer + 面板刷新）

- [x] 5.1 在 `src/stores/app-store.ts` 的 SSE reducer 新增 `guide_side_effect` 事件处理：按 `target` 字段触发对应面板的刷新标志
- [x] 5.2 各面板组件监听刷新标志并重新 fetch：Agents 面板 `fetchAgents()`、Skills 面板 `fetchSkills()`、MCP 面板 `fetchMcpServers()`、知识库面板 `fetchDocuments()`、记忆面板 `fetchMemories()`、Profile `fetchProfile()`/`fetchSettings()`、会话列表 `fetchConversations()`
- [x] 5.3 验证副作用通知不影响工作会话（guide_side_effect 只触发面板刷新，不改 activeConversationId）

## 6. 打磨与边界（M6：压缩 + Key 预配 + 测试）

- [x] 6.1 确认小A会话接入现有 `compact_pipeline`（Run 内压缩）：guide 会话的 token 预算与普通会话一致，超限触发压缩
- [x] 6.2 验证小A开箱即用：小A 的 provider/model/key/baseUrl 从 `GUIDE_AGENT_*` 环境变量读取（默认 deepseek，可切 LongCat 等 openai-compatible），deepseek provider 走 `DEEPSEEK_API_KEY` 三层链，openai-compatible 走 per-agent key
- [x] 6.3 验证小A会话不出现在全局搜索里（`mode='guide'` 过滤）
- [x] 6.4 验证小A不能修改/删除 builtin Agent（`manage_agents` update/delete 对 `is_builtin=True` 拒绝）
- [x] 6.5 验证小A不能改自己（`manage_agents` update 对 `is_guide=True` 拒绝）
- [x] 6.6 同步更新 spec 文档：`specs/01-core-entities.md`（Agent 加 is_guide，Conversation mode 加 guide）、`specs/02-stream-events.md`（加 guide_side_effect）、`specs/07-tools.md`（加 7 个管理工具）、`specs/05-adapter-interface.md`（baseline 跳过）、`specs/08-db-schema.md`（agents 表加 is_guide）、`specs/09-frontend-architecture.md`（双活跃会话 + 悬浮面板）
- [ ] 6.7 后端跑 `ruff check .` 和 `pytest` 通过
- [ ] 6.8 前端跑 `pnpm typecheck` 和 `pnpm lint` 通过
- [ ] 6.9 E2E 测试：首次登录引导、工作途中管理 Agent、智能整理记忆、管理知识库、整理偏好、活动回顾 + 消息搜索
