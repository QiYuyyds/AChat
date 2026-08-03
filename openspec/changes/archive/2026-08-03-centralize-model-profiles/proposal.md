## Why

模型选择（provider / model_id / api_key / base_url）目前写死在每个 Agent 上，创建/编辑 Agent 时必填，会话期间不可切换。用户每建一个 Agent 都要重填一遍 key 和 url，且无法在对话中按需换模型。把模型从 Agent 身份里剥离成独立的可复用「模型档」（ModelProfile），让用户集中配置 + 测试连通性 + 在输入栏运行时切换，能显著降低 Agent 创建成本并提升多模型协作的灵活性。

## What Changes

- **新增 ModelProfile 实体**：用户级可复用模型配置（name / provider / model_id / api_key / api_base_url / is_default / last_test_status / last_tested_at），存独立 `model_profiles` 表。**BREAKING**（新 DB 表）。
- **新增「模型」独立 Tab**：ModelProfile 的 CRUD UI + 连通性测试（发最小 chat completion ping，返回 ok/fail + 延迟）。
- **Agent 实体移除模型字段**：删除 `model_provider` / `model_id` / `api_key` / `api_base_url` / `supports_vision`，Agent 只保留人设（system_prompt / tools / capabilities / adapter_name）。**BREAKING**（DB 列删除 + 创建/编辑校验变更）。
- **运行时模型注入**：`build_adapter_input` 的模型/key 解析改为从 ModelProfile 取，不再读 Agent 字段。
- **输入栏模型选择器（plan B）**：每条消息可带 `modelProfileId`，运行时注入到本次 run。群聊中应用到 @ 提到的 SDK agent。
- **模型解析优先级**：① 消息显式选的 profile → ② 用户 default profile → ③ 用户一个 profile 都没配则拒绝运行 SDK agent（清晰报错引导去配置）。
- **CLI agent 不参与模型选择**：Claude Code / Codex 走 CLI 本地自带模型，AChat 不传 `--model`，CLI 用其 OAuth 账号默认模型。
- **向后迁移**：升级时为每个带 baked-in 模型的老 Agent 自动派生一个 ModelProfile 并标记 default，避免老用户升级后全部拒绝运行。

## Capabilities

### New Capabilities

- `model-profiles`: 用户级模型档实体与生命周期——ModelProfile 的存储、CRUD、连通性测试、default 标记、运行时解析优先级（显式选 → default → 拒绝）。

### Modified Capabilities

- `adapters`: 模型/key 解析路径变更——`build_adapter_input` 的 model_id / model_provider / api_key / api_base_url 从 ModelProfile 取而非 Agent；CLI adapter 不再注入 `--model`，CLI 用本地默认模型；`supports_vision` 从 Agent 挪到 ModelProfile。
- `agent-builder`: 创建/编辑 Agent 不再要求 model_provider / model_id / api_key / api_base_url，Custom adapter 不再强校验这些字段；Agent 变为纯人设。
- `persistence`: 新增 `model_profiles` 表（user_id 隔离）；Agent 表移除 model_provider / model_id / api_key / api_base_url / supports_vision 列；自动迁移脚本为老 Agent 派生 ModelProfile。
- `frontend`: 输入栏新增模型选择器（plan B，每条消息可带 profileId）；新增「模型」独立 Tab（CRUD + 连通性测试）；会话级状态承载当前选中 profile。
- `core-domain`: Agent 实体字段缩减——移除模型相关字段，Agent = 人设 + adapter_name + tools。

## Impact

- `backend/app/db/models.py` — 新增 `ModelProfile` 模型；`Agent` 移除 5 个模型列；`AgentRun` 已有 model_id/model_provider 记录，无表结构变更。
- `backend/app/db/migrations/` — 新增迁移：建 `model_profiles` 表 + 老数据派生迁移 + 删 Agent 模型列。
- `backend/app/services/agent_runner.py` — `build_adapter_input` 重写模型/key 解析（L3320-3331 SDK key 链、L3461-3465 custom_config、L3495 model_id、L2160 _run_react_loop 调用、L3510 _pick_settings_key）；`_get_agent_model_limit` 改为从 profile 取。
- `backend/app/api/agents.py` — 创建/编辑移除 model_provider/model_id/api_key/api_base_url 校验与写入；序列化去掉这些字段。
- `backend/app/api/conversations.py` — `send_message` 接受可选 `modelProfileId`，传入 run。
- `backend/app/api/model_profiles.py` — 新增：ModelProfile CRUD + `POST /{id}/test` 连通性测试。
- `backend/app/adapters/claude_adapter.py` — `if input.model_id: --model` 分支（L209-210）变为 CLI 不注入模型（input.model_id 恒 None），保留 `DEFAULT_CLAUDE_MODEL` 作 usage 回填。
- `backend/app/adapters/codex_adapter.py` — `model": input.model_id or None`（L174）同理，CLI 走 codex 默认。
- `src/components/message-input.tsx` — 新增模型选择器下拉。
- `src/components/model-profiles-panel.tsx` — 新增「模型」Tab 面板。
- `src/stores/app-store.ts` — 新增 modelProfiles 状态 + 当前会话选中 profileId。
- `src/shared/model-registry.ts` / `backend/app/utils/model_registry.py` — 无结构变更（仍是静态查表，ModelProfile 的 model_id 走同一查表）。
- `openspec/specs/adapters/spec.md` / `agent-builder/spec.md` / `persistence/spec.md` / `frontend/spec.md` / `core-domain/spec.md` — 同步 delta。
- **依赖**：`enhance-claude-cli-adapter` change 必须先提交/archive，两者都改 `build_adapter_input` 与 `claude_adapter._build_args`，本 change 在其基础上继续改。
