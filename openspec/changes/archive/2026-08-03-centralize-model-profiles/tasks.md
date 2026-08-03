## 1. 前置与顺序

- [x] 1.1 确认 `enhance-claude-cli-adapter` change 已提交并 archive（其 `cli_session_id` 列 + `build_adapter_input` session resume 改动已落地）
- [x] 1.2 基于 `enhance-claude-cli-adapter` 提交后的代码拉新分支 `feat-centralize-model-profiles`

## 2. DB schema — 新增 model_profiles 表

- [x] 2.1 在 `backend/app/db/models.py` 新增 `ModelProfile` 模型（id, user_id, name, provider, model_id, api_key, api_base_url, is_default, supports_vision, last_test_status, last_tested_at, created_at, updated_at）
- [x] 2.2 在 `model_profiles` 上加 `user_id` 索引 + `is_default` 唯一约束（per user 至多一条 default，用 partial unique index 或应用层保证）
- [x] 2.3 在 `backend/app/db/migrations/` 新增迁移脚本：建 `model_profiles` 表（此阶段不删 Agent 列，先共存）

## 3. 后端 ModelProfile CRUD + 连通性测试

- [x] 3.1 新增 `backend/app/schemas/model_profile.py`：Pydantic 模型（Create / Update / Out），Out 模型不回传明文 api_key（只回 masked 或 last4）
- [x] 3.2 新增 `backend/app/api/model_profiles.py`：`GET /api/model-profiles`（列表，按 user_id 过滤）、`POST /api/model-profiles`（创建）、`PATCH /api/model-profiles/{id}`（更新）、`DELETE /api/model-profiles/{id}`（删除，删 default 时自动转 default）
- [x] 3.3 实现 default 标记逻辑：设置 `is_default=true` 时同 user 其他行置 false；删除 default 且还有其他 profile 时自动转最早一条
- [x] 3.4 实现 `POST /api/model-profiles/{id}/test` 连通性测试：用 profile 的 provider/model_id/api_key/api_base_url 发最小 chat completion（max_tokens=1, timeout 5s），返回 `{ status, latencyMs, error? }`
- [x] 3.5 连通性测试限流：同一 profile 测试间隔 ≥ 3s（后端 per-profile 冷却 + 前端 debounce）
- [x] 3.6 测试后更新 `last_test_status` / `last_tested_at`
- [x] 3.7 注册 model_profiles router 到 `backend/app/api/__init__.py` 或 main app

## 4. 后端 build_adapter_input 模型解析重写

- [x] 4.1 在 `build_adapter_input` 新增 ModelProfile 解析逻辑：接收 `model_profile_id` 参数，查 `model_profiles` 表（按 user_id 隔离）
- [x] 4.2 SDK 分支模型解析：从 ModelProfile 取 model_id / model_provider / api_key / api_base_url / supports_vision，替换 `agent.model_id` / `agent.model_provider` / `agent.api_key` / `agent.api_base_url` 引用（L3320-3331 SDK key 链、L3461-3465 custom_config、L3495 AdapterInput model_id）
- [x] 4.3 实现 plan B 优先级：message 带 profile_id → 用该 profile；没带 → 用 user default profile；user 零 profile → raise 清晰错误
- [x] 4.4 改 `_run_react_loop` 调用（L2160）：`agent.model_id` / `agent.model_provider` 改为从解析出的 profile 取
- [x] 4.5 改 `_get_agent_model_limit`（L386-398）：从解析出的 profile 取 model_provider/model_id 查 model_registry
- [x] 4.6 改 `_pick_settings_key`（L3510）：SDK 路线不再用（key 从 profile 取），CLI 路线保留原逻辑或简化
- [x] 4.7 处理引用的 profile 已删 → 回退 default profile + 发警告事件
- [x] 4.8 处理 default 也不存在 → 拒绝运行，返回清晰错误

## 5. 后端 send_message 接受 modelProfileId

- [x] 5.1 在 `backend/app/api/conversations.py` `send_message` 解析请求体新增可选 `modelProfileId` 字段
- [x] 5.2 将 `modelProfileId` 透传到 RunArgs / 触发 run 的调用链
- [x] 5.3 在 `RunArgs` dataclass（agent_runner.py ~L470）新增 `model_profile_id: str | None = None` 字段

## 6. 后端 CLI adapter 不再注入 --model

- [x] 6.1 `claude_adapter.py` `_build_args`：`if input.model_id: --model` 分支保留但 input.model_id 对 CLI 恒 None（不传 --model）；`DEFAULT_CLAUDE_MODEL` 保留用于 usage 回填
- [x] 6.2 `codex_adapter.py`：`model": input.model_id or None` 对 CLI 恒 None；codex 默认用于 usage 回填
- [x] 6.3 验证 CLI agent 走 OAuth 本地默认模型，不受 ModelProfile 影响

## 7. 后端 agents.py 移除模型字段

- [x] 7.1 创建 Agent：移除 `model_provider` / `model_id` / `api_key` / `api_base_url` 校验与写入（删 `if not (model_provider and model_id): 400` 强校验）
- [x] 7.2 编辑 Agent：移除 model 字段的 patch 逻辑
- [x] 7.3 序列化 Agent（`_serialize_agent`）：去掉 `modelProvider` / `modelId` / `apiKey` / `apiBaseUrl` 字段
- [x] 7.4 `validate_openai_compatible_api_key` / `validate_openai_compatible_base_url` 校验迁到 ModelProfile CRUD

## 8. 前端 — 「模型」Tab

- [x] 8.1 新增 `src/components/model-profiles-panel.tsx`：列表（name + provider + model_id + test status badge）+ create/edit dialog + delete + 设 default
- [x] 8.2 新增 `src/lib/api.ts` 中 ModelProfile CRUD + test 的 API 调用函数
- [x] 8.3 在应用 shell 侧栏注册「模型」Tab（与 Analytics / Settings 同级）
- [x] 8.4 连通性测试按钮：调用 `POST /{id}/test`，inline 显示 ok/fail + latency
- [x] 8.5 api_key 输入框 mask + 只显示 last4，编辑时不回填明文

## 9. 前端 — 输入栏模型选择器

- [x] 9.1 在 `src/stores/app-store.ts` 新增 `modelProfiles: Record<string, ModelProfile>` 状态 + `selectedProfileIdByConv` 映射
- [x] 9.2 在 `src/components/message-input.tsx` 输入栏加模型选择器下拉（列出 user 的 profiles）
- [x] 9.3 仅当会话含 SDK (Custom) agent 时显示选择器；CLI-only 会话隐藏
- [x] 9.4 零 profile 时显示空态 + 引导去「模型」Tab 配置，禁用发送
- [x] 9.5 发送时把 `modelProfileId` 附到 `sendMessage` 请求体
- [x] 9.6 `src/shared/types.ts` 新增 ModelProfile 类型 + `src/db/schema.ts` 同步（若有本地镜像）

## 10. 迁移老 Agent baked-in 模型

- [x] 10.1 新增迁移脚本：扫描 `agents` 表 `model_provider IS NOT NULL` 的行
- [x] 10.2 按 (user_id, provider, model_id, api_key, api_base_url) 去重插入 `model_profiles`
- [x] 10.3 每 user 标记最早创建的一条为 default
- [x] 10.4 builtin agent（user_id IS NULL）的模型配置不迁移（builtin 走各自路径，单独处理或留空）
- [x] 10.5 验证迁移后老 Agent 仍可运行（模型从派生的 ModelProfile 解析）

## 11. 删除 Agent 模型列（最终，确认无回归后）

- [x] 11.1 迁移脚本删除 `agents` 表的 `model_provider` / `model_id` / `api_key` / `api_base_url` / `supports_vision` 列
- [x] 11.2 `backend/app/db/models.py` Agent 模型移除对应 Mapped 字段
- [x] 11.3 `backend/app/services/agent_runner.py` 确认无残留 `agent.model_*` / `agent.api_*` 引用
- [x] 11.4 全局 grep `agent.model_provider` / `agent.model_id` / `agent.api_key` / `agent.api_base_url` 确认无残留

## 12. 测试

- [x] 12.1 单测：ModelProfile CRUD（创建 / 设 default 唯一性 / 删 default 自动转）
- [x] 12.2 单测：连通性测试 ok / fail / 限流
- [x] 12.3 单测：`build_adapter_input` 显式 profile → default → 零 profile 拒绝
- [x] 12.4 单测：引用已删 profile → 回退 default + 警告
- [x] 12.5 单测：CLI agent 不传 --model（input.model_id 恒 None）
- [x] 12.6 集成测：solo SDK 会话输入栏选模型 → run 用该模型
- [x] 12.7 集成测：群聊 @ 多个 SDK agent → profile 应用到所有 @ 的 SDK agent
- [x] 12.8 集成测：CLI-only 会话输入栏无选择器，CLI 用本地默认模型
- [x] 12.9 集成测：老 Agent 迁移后可运行（迁移脚本 + 运行验证）

## 13. Spec 同步与收尾

- [x] 13.1 `ruff check .` 通过
- [x] 13.2 `pytest` 通过
- [x] 13.3 `pnpm typecheck` + `pnpm lint` 通过
- [x] 13.4 同步 delta specs 到主 specs（`openspec/specs/` 下 adapters / agent-builder / persistence / frontend / core-domain + 新增 model-profiles）
- [x] 13.5 更新 `CLAUDE.md` §3.7（Custom Agent 工具架构）与 §5.4（API Key 管理）相关描述，反映 model 字段移除 + ModelProfile 解析
- [x] 13.6 更新 `specs/01-core-entities.md` / `specs/08-db-schema.md` / `specs/05-adapter-interface.md` 编号版 spec
- [x] 13.7 运行 `openspec archive` 收尾（或按项目惯例手动 archive）
