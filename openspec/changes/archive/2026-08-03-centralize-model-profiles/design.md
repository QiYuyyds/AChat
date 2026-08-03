## Context

当前架构中，模型是 Agent 身份的一部分：`Agent` 实体持有 `model_provider` / `model_id` / `api_key` / `api_base_url` / `supports_vision`，创建/编辑时必填（Custom adapter 有强校验），会话期间不可切换。key 解析在 `build_adapter_input` 里走四层链（agent → user_settings → env → CLI OAuth）。

`AgentRun` 表已按 run 记录 `model_id` / `model_provider`，说明"每次 run 可用不同模型"在数据层已预留，但运行时从未利用过这个能力——模型始终来自 Agent 记录。

`user_settings` 表存了 per-user 的 provider key（每 provider 一个 key），装不下"一个 provider 配多个模型档"的诉求。`model_registry` 是静态查表（context window + 定价），非用户可配。

约束：本 change 必须在 `enhance-claude-cli-adapter` 提交/archive 之后进行，两者都改 `build_adapter_input` 与 `claude_adapter._build_args`。

## Goals

- 把模型配置从 Agent 身份剥离成独立的、用户级可复用的 ModelProfile 实体
- 用户可在「模型」Tab 集中配置多个模型档并测试连通性
- 会话输入栏支持每条消息选模型（plan B），群聊中应用到 @ 的 SDK agent
- CLI agent（Claude Code / Codex）不参与模型选择，走 CLI 本地自带模型
- 老用户升级无感：已有 Agent 的 baked-in 模型自动派生为 ModelProfile

## Non-Goals

- 不改 model_registry 的静态查表结构（context window / 定价仍硬编码，ModelProfile 的 model_id 走同一查表）
- 不引入模型路由 / 自动选模型策略（用户手动选）
- 不改 RAG / 记忆系统的 `EMBEDDING_API_KEY` / `LLM_API_KEY` 配置（那是独立路径）
- 不动 CLI agent 的 CLI 本体模型配置（用户自行在 claude/codex CLI 侧配置）
- 不做跨用户的模型档共享

## Decisions

### D1: 新建 `model_profiles` 表，而非扩展 user_settings JSON

**选择**：新建独立表。

**理由**：ModelProfile 有独立生命周期（CRUD + 连通性测试 + default 标记 + 前端选择器按 id 引用），且一个 provider 需要支持多个档（如用户有多个 deepseek key 或多个 openai 组织）。`user_settings` 是列式 K/V（每 provider 一个 key），扩成 JSON 数组破坏现有结构且查询不便。

**替代方案**：在 `user_settings` 加 `model_profiles_json` 列——被否，因为 profile 需要被 `model_profile_id` 外键引用（消息、会话默认值），JSON 列无法做引用。

### D2: Agent 模型字段全删（X-strict），CLI agent 不注入 --model

**选择**：Agent 实体移除 `model_provider` / `model_id` / `api_key` / `api_base_url` / `supports_vision`。CLI adapter 的 `if input.model_id: --model` 分支变为不注入（input.model_id 恒 None），CLI 用 OAuth 账号默认模型。

**理由**：用户明确"CLI agent 走本地，AChat 不为其选择模型"。X-strict 让 Agent = 纯人设，模型完全运行时注入，语义干净。CLI 不传 `--model` = CLI 本体决定模型，与"本地自己配了模型"一致。

**替代方案**：X-SDK-only（只删 Custom 的模型字段，CLI 保留 model_id）——被否，因为两套规则（SDK 删 / CLI 留）增加心智负担，且用户明确 CLI 不需要 AChat 管模型。

**代价**：用户无法再在 AChat 里给 Claude Code 指定具体 claude 模型（须去 CLI 本体配置）。可接受。

### D3: 模型选择粒度 = 每条消息（plan B）

**选择**：`send_message` 接受可选 `modelProfileId`，注入到本次 run。

**理由**：用户要"方便切换"，每条消息能换模型最灵活。群聊中该 profile 应用到 @ 提到的 SDK agent。

**替代方案**：会话级模型（一个会话一个模型整轮用）——被否，不够灵活，且 CLI session resume 不受影响（CLI 根本不传模型）。

### D4: 模型解析优先级 = 显式选 → default profile → 拒绝运行

**选择**：
1. 消息带了 `modelProfileId` → 用该 profile
2. 没带 → 用用户的 default profile（`is_default=true` 的那条）
3. 用户一个 profile 都没配 → 拒绝运行 SDK agent，返回清晰错误引导去「模型」Tab 配置

**理由**：覆盖"不选也能发"的 just-send 场景，同时强制至少配一个 profile（符合用户"没配就拒绝"的要求）。

**边界**：default profile 被删除时——若该用户还有其他 profile，自动选最早创建的一条为新 default；若一条不剩，回到拒绝运行。

### D5: supports_vision 从 Agent 挪到 ModelProfile

**选择**：`supports_vision` 字段移到 ModelProfile，`CustomConfig` 从 profile 取。

**理由**：视觉能力是模型属性，不是人设属性。同一个 Agent 人设用不同 profile 时，视觉能力应随 profile 变。

### D6: 连通性测试 = 最小 chat completion ping

**选择**：`POST /api/model-profiles/{id}/test` 用该 profile 的 key/url/model 发一个单轮最小请求（如 `messages=[{"role":"user","content":"ping"}]`, `max_tokens=1`），返回 `{ status: "ok"|"fail", latencyMs, error? }`。

**理由**：最小 token 消耗验证 key + url + model 三者都通。失败判定：HTTP 非 2xx 或超时（5s）= fail。

**限流**：每个 profile 测试间隔 ≥ 3s（前端 debounce + 后端 per-profile 冷却），避免狂点烧钱。

### D7: 自动迁移老 Agent 的 baked-in 模型

**选择**：迁移脚本扫描所有 `model_provider IS NOT NULL` 的 Agent，按 (user_id, provider, model_id, api_key, api_base_url) 去重派生 ModelProfile，每个 user 标记最早的一条为 default。

**理由**：避免老用户升级后"一个 profile 都没配 → 全部拒绝运行"。

## Risks / Trade-offs

- **[风险] Agent 实体删列是破坏性变更** → 迁移脚本先派生 ModelProfile 再删列；提供回滚脚本（从 ModelProfile 反向填回 Agent 列，仅在未删列前可用）。
- **[风险] default profile 误删导致 SDK agent 拒绝运行** → 删 default 时前端二次确认；后端删 profile 时若它是 default 且还有其他 profile，自动转 default。
- **[风险] 消息级 modelProfileId 引用了已删的 profile** → run 时解析失败，回退到 default profile 并发警告事件；若 default 也不存在则拒绝运行。
- **[风险] 与 enhance-claude-cli-adapter 改同一片代码** → 严格顺序：cli-adapter 先提交/archive，本 change 在其基础上改；`build_adapter_input` 改不同行（模型解析 vs session resume）。
- **[风险] 群聊 per-agent 模型粒度 UX 复杂** → 第一版只做"一条消息一个 profile，应用到所有 @ 的 SDK agent"；per-mention 绑定不同 profile 留作后续增强。
- **[代价] CLI agent 用户失去在 AChat 指定模型的能力** → 文档说明去 CLI 本体配置；`DEFAULT_CLAUDE_MODEL` / codex 默认仍用于 usage 回填。

## Migration Plan

1. **前提**：`enhance-claude-cli-adapter` 已提交并 archive（其 `cli_session_id` 列 + `build_adapter_input` 的 session resume 改动已落地）。
2. **建表**：新建 `model_profiles` 表（不删 Agent 列，先共存）。
3. **派生迁移**：扫描 `agents` 表 `model_provider IS NOT NULL` 的行，按 (user_id, provider, model_id, key, url) 去重插入 `model_profiles`，每 user 标记最早一条 default。
4. **后端切换**：改 `build_adapter_input` 从 ModelProfile 解析模型/key（CLI 不注入 --model）；改 `agents.py` 创建/编辑不再写模型字段；新增 `model_profiles.py` API。
5. **前端切换**：输入栏加模型选择器；新增「模型」Tab；app-store 加 modelProfiles 状态。
6. **删列**：确认无回归后，迁移脚本删 Agent 的 5 个模型列。
7. **回滚**：步骤 6 之前可回滚（重建 Agent 列从 ModelProfile 反填）；步骤 6 之后回滚需从 ModelProfile 反向重建。

## Open Questions

- per-mention 绑定不同 profile 的群聊 UX 是否在第一版之后做？（当前 Non-Goal，但用户提过"某个 agent 某个模型"——第一版用"一条消息一个 profile 应用到所有 @ 的 SDK agent"近似，后续可增强。）
