# Design: Add Guide Agent

## Context

AChat 的管理操作（建/改 Agent、管 Skill/MCP/知识库、整理记忆、改画像）目前全部是 REST API + 人工点 UI。现有架构已具备所有支撑能力：builtin agent 标记位（`is_builtin` / `is_orchestrator`）、custom adapter SDK 路线、统一 Agent Loop（`run_agent_loop(mode='solo')`）、`ask_user` 结构化确认、SSE 单连接按 conversationId 分桶、`ToolContext.user_id` 隔离、三层 API Key 优先级、记忆 `consolidate()` 算法驱动去重。

本设计在此基础上引入"小A Agent"——一个 builtin + guide 标记的管理引导 Agent，以全局悬浮助手形态常驻。不引入新 adapter、不写独立服务路径、不改安全约束。

**约束**：
- CLAUDE.md §3.1 五层分层：UI 不直接调 LLM，工具执行属 L3
- CLAUDE.md §3.6 统一 Agent Loop：所有模式走同一个 `run_agent_loop`
- CLAUDE.md §3.7 Custom Agent 工具架构：baseline + 可选；guide agent 跳过 baseline
- CLAUDE.md §3.8 RAG/记忆是可选增强，降级不阻断核心对话
- CLAUDE.md §5.5 用户数据隔离：`user_id` 列隔离

## Goals

- 用户用自然语言驱动管理操作，无需手动点 UI
- 小A 开箱即用（项目预配 `DEEPSEEK_API_KEY`，用户无需填 Key）
- 小A 能 LLM 驱动地智能整理记忆/偏好（剔除垃圾 + 合并重复 + 提炼升华）
- 悬浮面板与工作会话并存，互不干扰（双活跃会话）
- 小A 只做管理，不碰代码/文件/产物，不改安全约束
- 最大化复用现有架构，零破坏性变更

## Non-Goals

- 小A不替代工作 Agent（不写代码、不跑命令、不产出 artifact）
- 小A不替代 Orchestrator（不拆任务、不派发子 Agent）
- 小A不修改安全约束（黑名单、沙箱规则）
- 小A不管理其他用户的数据（严格 `user_id` 隔离）
- 小A不主动推送通知（v1 被动响应，事件驱动留未来）
- 不做多个小A会话（单例，历史持续累积）
- 不做首页/落地页引导（方案 ③ 留未来叠加）

## Decisions

### Decision 1: `is_guide` 标记位而非 `agent_class` 枚举

**选择**：Agent 表新增 `is_guide: bool` 布尔字段，与 `is_builtin` / `is_orchestrator` 并列。

**理由**：
- 现有 `is_orchestrator` 已被多处引用（agent_runner、API、前端），迁移到 `agent_class` 枚举改动面大
- `is_guide` 与 `is_orchestrator` 保持一致的演进风格
- 等第三个特殊类型出现时再重构为 `agent_class`

**备选**：`agent_class` 枚举（worker / orchestrator / guide）更可扩展，但要迁移现有字段，违反"小步"原则。

### Decision 2: 复用 custom adapter，不新增 adapter 类型

**选择**：小A `adapter_name='custom'`，复用 SDK 路线的 LLM 调用链。

**理由**：
- adapter 本应是"LLM 接入方式"（CLI / SDK / Mock），小A 只是工具集不同
- 新增 adapter 会拉伸语义，且要在 registry 注册一个其实复用 custom 的壳
- baseline 跳过靠 `is_guide` 标记位控制，不需要 adapter 级区分

**备选**：新增 `guide` adapter 类型——语义拉伸，无实际收益。

### Decision 3: 悬浮面板（方案 ②）而非普通会话（方案 ①）或首页（方案 ③）

**选择**：小A 以全局悬浮面板常驻，不进入普通会话列表。

**理由**：
- 方案 ① 把小A放进会话列表，用户要自己找到/开启，丧失"门面引导"价值
- 方案 ③ 改动最大，老用户可能不需要引导想直接干活
- 方案 ② 在不阻断工作流的前提下提供引导，对新老用户都友好
- 首次登录自动展开，引导性强

### Decision 4: `mode='guide'` 会话 + list 过滤

**选择**：`Conversation.mode` 新增 `'guide'` 值，`list_conversations` 过滤掉 guide 会话。

**理由**：
- `mode` 是字符串字段（非数据库枚举），加值无需 DDL
- guide 会话是单例（一个用户一个 `guideConversationId`），不出现在会话列表
- guide 会话不可被用户删除（删除时检查 `mode`）
- 历史 SSE 事件天然按 conversationId 分桶，双会话共存无需额外连接

### Decision 5: 记忆 optimize 是 LLM 驱动，工具只执行 plan

**选择**：`manage_memory(action=optimize)` 接收一个 `plan`（delete_ids + merge_groups + update_ids），工具 handler 只执行删除/新建/更新。智能分析在小A LLM 侧完成。

**理由**：
- 现有 `consolidate()` 是纯算法：0.95 余弦去重 + TTL 过期 + importance 衰减
- 它抓不到表述不同但语义重复的记忆（"用户偏好 TypeScript" vs "用户喜欢用 TS"）
- 它识别不了垃圾内容（"test"、空洞内容）
- LLM 能理解语义、识别垃圾、合并分散记忆、提炼升华
- 智能放 LLM 侧，工具只做执行——分工清晰，工具可单元测试
- 合并后新记忆通过 `long_term.add()` 生成 embedding，降级时无 embedding 但内容仍在

**与 `consolidate()` 互补**：

| 维度 | `consolidate()` | `optimize` |
|---|---|---|
| 触发 | 自动（每 5 条） | 用户主动 |
| 去重 | 0.95 余弦 | LLM 语义 |
| 合并 | 只能完全重复 | 能合并分散记忆 |
| 垃圾识别 | 无 | 有 |
| 用户参与 | 无 | 必须确认 |

### Decision 6: `guide_side_effect` SSE 事件

**选择**：新增 `guide_side_effect` 事件类型，前端收到后按 `target` 字段刷新对应面板。

**理由**：
- 精准刷新（只刷新受影响的面板），而非全量 refetch
- 事件已按 conversationId 分桶，前端 reducer 自然路由到 guide 会话
- 事件是契约，加入 `StreamEvent` discriminated union

**备选**：前端在 `run.end` 后延迟 refetch 所有面板——更简单但可能多空请求，且刷新时机不精确（run 可能还在流式输出最后一段）。

### Decision 7: 空 sandbox workspace

**选择**：guide 会话创建一个空 sandbox workspace，小A工具不依赖 workspace。

**理由**：
- 不改 conversation 创建逻辑（workspace 是空的而已）
- 小A工具全用 `ToolContext.user_id` 隔离，不需要 workspace 路径
- 前端悬浮面板不渲染文件浏览器、不渲染产物预览

### Decision 8: 破坏性操作双重确认

**选择**：system prompt 要求 + 工具参数 `confirm` 字段校验。

**理由**：
- LLM 可能忘记先 `ask_user` 就直接调 `delete`
- 工具 handler 里做兜底：`delete` action 且 `confirm != true` 时返回错误
- 双重保险，system prompt 是软约束，参数校验是硬约束

### Decision 9: 开箱即用——环境变量 Key 兜底

**选择**：小A `api_key=NULL`，走 `get_effective_api_key` 的第三层（`os.environ["DEEPSEEK_API_KEY"]`）。

**理由**：
- 现有三层优先级不变：`agents.api_key` > `user_settings.deepseek_api_key` > 环境变量
- 用户若想用自费 Key，可在设置面板覆盖
- 项目方预配 `DEEPSEEK_API_KEY` 到部署环境，用户无需填写任何 Key

## Risks / Trade-offs

- **[builtin 种子新模式]** → 首次在 prod 引入 builtin agent 种子机制（之前只在 test fixture）。种子在 `main.py` lifespan startup 里幂等执行，检查 `is_guide=True` 是否存在再创建。降级：种子失败不阻断启动（try/except + warning log）。

- **[悬浮面板前端工作量]** → M4 是最大工作量块（拖拽/缩放/收起展开/快捷键/localStorage/精简 MessageList）。但 `GuideFloatingPanel` 是自包含组件，不侵入现有 `ChatPanel` 逻辑。可分阶段：先做基础面板（固定位置/展开收起），再加拖拽缩放。

- **[小A 会话历史膨胀]** → guide 会话是单例且持续累积。v1 接入现有 `compact_pipeline`（Run 内压缩）和 `context_compaction_service`。token 预算与普通会话一致，超限触发压缩。

- **[开箱即用 Key 谁付费]** → 业务决策（非技术）。`.env.example` 文档化 `DEEPSEEK_API_KEY`，真实 key 不进 git。用户可在设置面板填自己的 Key 覆盖。

- **[LLM 忘记确认]** → system prompt 是软约束。工具参数 `confirm` 字段校验是硬兜底。LLM 可能先 `ask_user` 再调 `delete(confirm=true)`，也可能直接调 `delete(confirm=false)` 被拒后补 `ask_user`——后者多一轮但安全。

- **[记忆合并 embedding 降级]** → 合并后新记忆通过 `long_term.add()` 生成 embedding。embedding 服务挂了降级为无 embedding（内容仍在，只是向量检索召回不到），符合 CLAUDE.md §3.8。

- **[`list_conversations` 过滤变更]** → 现有代码返回用户所有会话。加 `WHERE mode != 'guide'` 后，如果有其他代码依赖"列出全部会话"会受影响。已确认前端 `useConversationList` 从 store 读，store 由 `fetchConversations()` 填充，后端过滤是正确切入点。

## Migration Plan

1. **DB 迁移**（幂等）：`_migrate_columns` 加 `ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_guide BOOLEAN NOT NULL DEFAULT FALSE`。现有 Agent 默认 `False`，无行为变化。
2. **种子**：`main.py` lifespan startup 加 `_seed_guide_agent`，幂等检查后创建小A Agent。
3. **代码**：所有改动是加法性的——新字段默认 `False`、新工具仅对 guide agent 注入、新事件类型、新组件。
4. **回滚**：删除小A Agent 记录即可（`DELETE FROM agents WHERE is_guide = TRUE`）。`is_guide` 字段保留无害（默认 `False`）。

## Open Questions

- **小A会话压缩优先级**：guide 会话是单例且持续累积，是否需要把 `compact_pipeline` 接入提前到 M4 而非 M6？
- **`guide_side_effect` vs 简单 refetch**：是否值得新增事件类型？还是前端在 `run.end` 后延迟 refetch 更简单？（当前设计选择事件，因为精准刷新）
- **小A能否看到其他会话的消息内容**：`manage_conversations(action=get)` 返回消息摘要。跨会话读取虽是同用户数据，是否限制只返回摘要而非全文？（当前设计返回摘要）
- **小A system prompt 迭代**：小A不能改自己（§9.2）。system prompt 迭代只能改代码 + 重启。可接受，但要明确。
