## Context

custom_adapter 是项目唯一的 SDK 适配器（`adapter_name="custom"`），基于 `AsyncOpenAI` SDK 调用 deepseek / volcano-ark / openai / openai-compatible 等 OpenAI 兼容协议。这些协议不支持 Anthropic 的 `cache_control` 显式断点，而是**自动识别最长公共前缀**做隐式缓存（DeepSeek 返回 `prompt_cache_hit_tokens`，OpenAI 返回 `cached_tokens`）。

当前 prompt 拼装（`agent_runner.build_adapter_input`）已实现静态/动态分离：`render_static()`（仅 `SlotConstraints`）进 `system_prompt`，`render_dynamic()`（Profile/TaskMem/ToolState/Recall，包 `<system-reminder>`）进尾部 user 消息。`ContextSummary` 由 `compact_conversation` 生成，作为 `history[0]` 注入（pinned），当前**仅手动 `/compact` 触发**。

两个缺口：(1) 无任何会话元数据注入；(2) 上下文压缩不自动周期化。

## Goals / Non-Goals

**Goals:**

- 让 custom_adapter 的 prompt 获得语言/时区/时间感知。
- 让静态元数据（language/timezone）加入隐式缓存稳定前缀，动态元数据（current_time）尾部挂载不击穿前缀。
- 让 `ContextSummary` 在长会话中自动周期刷新，无需用户手动 `/compact`。
- 自动压缩静默不打扰用户，手动 `/compact` 保留公告。
- 复用现有 `compact_conversation` 的增量整合与 size gate，改动最小。

**Non-Goals:**

- 不改动 Profile 槽位（保持 `static=False` 进动态 `<system-reminder>`）。
- 不引入 `cache_control` 显式断点（OpenAI 兼容协议无此字段）。
- 不覆盖 CLI 适配器（claude-code/codex）链路——CLI 自管上下文与缓存。
- 不持久化 session metadata（运行时计算值，不入库）。
- 不改 `ContextSummary` 表结构。
- 不做 Append 策略（保留多个历史 summary 段）——见 Decisions。

## Decisions

### D1: 静态/动态元数据分离归属

**决策**：`language`/`timezone` 注入 `system_prompt`，`current_time` 注入尾部 user 消息。

**理由**：隐式缓存命中靠"前缀稳定"。language/timezone 会话级不变，进 system_prompt 即加入稳定前缀→缓存命中；current_time 每轮变，若进 system_prompt 会每轮击穿前缀→必须尾部挂载。

**备选**：三项全尾部——会损失 language/timezone 的缓存收益（虽小，但它们本可静态命中）。三项全 system——current_time 每轮击穿前缀，不可接受。分离归属是唯一兼顾两者者。

### D2: 元数据钝化（分桶）

**决策**：`current_time` 降频为 time_bucket（如 `Sunday_Morning`），不保留秒级精度；language 为 locale 标签（`zh-CN`）；timezone 为偏移标签（`GMT+8`）。

**理由**：钝化在尾部（不缓存区）的主收益是降 token 数 + 模型友好；若未来 current_time 需进缓存区，钝化是前提。静态项钝化为粗标签避免歧义。

**备选**：注入精确时间戳——token 更长，且若误置缓存区会每轮雪崩。

### D3: 触发器选水位计数（方案 D）

**决策**：外层触发条件为 `COUNT(msg.created_at > last_summary.covered_until_created_at) >= 10`，内层复用 `compact_conversation` 的 `MIN_COMPACT_TOKENS=800` / `MIN_COMPACTABLE=2` 兜底。

**理由**：水位计数预检成本极低（一条 `COUNT(*) WHERE` 查询，零 token 估算），且天然契合 `compact_conversation` 已是增量（keyed on `covered_until_created_at`）的事实。外层粗、内层准的两段式既便宜又稳健。

**备选**：
- B（token 阈值）——更准但每次预检需 `estimate_tokens` 遍历，成本中。
- C（A AND B 混合）——最准但复杂度最高，收益边际递减。
- A（纯轮次）——与 D 几乎同价，但 D 复用了 covered_until 水位语义，更内聚。

### D4: Replace 策略（不 Append）

**决策**：自动压缩复用 `compact_conversation` 现有增量整合行为——新 summary 替换 `history[0]` 旧 summary（内容上整合了旧 summary），不保留多个历史 summary 段。

**理由**：Replace 每次 history[0] 内容变→下一轮缓存 miss 一次后重新稳定。代价是"一次性"的（1 轮 miss + 1 次 LLM），收益是"复利"的（此后每轮少带 K 条历史 token），净正。Append（保留 `[summary_v1, summary_v2, ...]`）虽能让缓存前缀单调增长从不失效，但多 Agent 群聊里多段 summary 易冗余/矛盾，且需改 `get_latest_context_summary`（当前 `ORDER BY desc LIMIT 1`）与注入逻辑，改动量大。

**备选**：Append——缓存稳定性更高，但 summary 冗余/矛盾在群聊场景刺手，实现改动大，性价比不足。

### D5: 静默模式的实现缝

**决策**：给 `compact_conversation` 加 `silent: bool = False` 参数。`silent=True` 跳过"步骤 i"（[第258-319行](file:///d:/java/project/bitdance-agenthub-main/backend/app/services/context_compaction_service.py#L258-L319) 的系统消息插入 + `MessageAddedEvent` 广播），仅执行"步骤 a-h"（核心压缩）。`CompactResult.message` 在 silent 模式为 `None`，`summary`/`ctx_before`/`ctx_after` 照常返回。

**理由**：`compact_conversation` 结构天然分两段，缝在第 258 行，改动极小，不碰核心压缩逻辑。手动 `/compact` 走默认 `silent=False` 保留公告。

### D6: post-run 异步钩子 + 子 Agent 豁免

**决策**：新增 `_maybe_auto_compact_hook`，作为 `execute_run` 的 `asyncio.create_task` 后置执行（与现有 `_post_run_memory_hook` / `_maybe_generate_summary_hook` 并列）。`args.override_prompt` 非空时豁免。异常 best-effort 捕获记 warning。

**理由**：复用既有 post-run 钩子模式；子 Agent run 是 Orchestrator 派发的隔离任务，不应副作用整条会话上下文。异步非阻塞保证不拖慢主对话。

### D7: 元数据来源——Settings 环境变量（不透传 HTTP 头）

**决策**：`language`/`timezone` 从 `Settings` 读取——在 [`config.py`](file:///d:/java/project/bitdance-agenthub-main/backend/app/config.py) 新增两个字段：`default_language: str = "zh-CN"`、`default_timezone: str = "GMT+8"`（走 `.env` / `.env.local`，与现有字段同机制）。`build_adapter_input` 在 SDK 分支直接调 `get_settings()` 读取，零透传链路，不经 PromptAssembler。

**理由**：调研触发链路（`send_message` API 有 `req: Request` → `conversation_service.send_message` **签名不收 Request** → `runner.run` → `execute_run` → `build_adapter_input`）发现 HTTP `Request` 对象在 service 层已丢失。要用请求头需跨四层透传，改动大且破坏现有签名。项目定位 local-first 桌面应用（Electron），部署级 = 会话级（单用户），Settings 环境变量足够。spec 只约束"注入位置与钝化形态"，不约束来源，故 D7 是实现决策，不改 spec。

**备选**：(a) HTTP `Accept-Language` 头——真实反映客户端偏好，但跨四层透传改动大，且 `Accept-Language` 不携带时区信息。(b) `Conversation` 表加字段——per-会话可定制，但破坏 proposal 的"无 DB schema 变更"约束，需前端 UI 配合。两者性价比均不足。

### D8: current_time 取服务器时间

**决策**：`current_time` 取服务器本地时间（`datetime.now()`），在 `build_adapter_input` 拼装时实时计算并钝化。

**理由**：prompt 在服务器拼装，服务器时间确定可靠、零依赖、零透传。客户端时间不可信（时钟漂移、恶意篡改），且需从 HTTP 层透传自定义头（如 `X-Client-Time`），成本高。current_time 钝化后只需粗粒度桶（见 D9），服务器时间精度绰绰有余。

**备选**：客户端时间——需透传，且客户端时钟不可信，得不偿失。

### D9: time_bucket 分桶粒度——星期 + 四时段（28 桶）

**决策**：`current_time` 钝化为 `{Weekday}_{Period}` 形式（如 `Sunday_Morning`）。`Weekday` 取 `datetime.now().strftime("%A")`（`Monday`..`Sunday`）；`Period` 按本地小时切分四桶：

| Period | 小时范围（含起不含止） |
|---|---|
| `Morning` | 05:00 – 11:59 |
| `Afternoon` | 12:00 – 17:59 |
| `Evening` | 18:00 – 22:59 |
| `LateNight` | 23:00 – 04:59 |

共 7×4 = 28 个桶。边界：18:00 归 `Evening`（18 点已是下班时段），23:00 归 `LateNight`，05:00 归 `Morning`。

**理由**：星期赋予"工作日 vs 周末"语义，时段赋予"活跃 vs 休息"语义，组合信息量大于仅时段（4 桶）。28 桶仍在钝化范围（远小于秒级精度），且桶数有限不击穿缓存（current_time 本就在尾部不缓存区）。spec 的钝化 Requirement 已示例 `Sunday_Morning`，与 D9 一致。

**备选**：仅时段（`Morning`/`Afternoon`/...）——4 桶太粗，丢失"周末"语义；精确到小时——24×7=168 桶过细，钝化意义削弱。

## Risks / Trade-offs

- **[自动压缩引发缓存雪崩]** → 每次压缩 history[0] 变更，下一轮 cache miss 一次。**缓解**：水位 N=10 控制频次（每 ~10 轮 1 次 miss），且 miss 后立即重新稳定；代价一次性 vs 收益复利，净正。
- **[异步竞态]** → 自动压缩未完成时下一轮读到旧 summary。**缓解**：最终一致，系统不出错；miss 落到读到新 summary 的那一轮，可接受（spec 已明确）。
- **[双 LLM 后置调用]** → 首次回复轮同时有 `_maybe_generate_summary_hook`（侧边栏标题）与未来可能的本 hook。**缓解**：侧边栏标题 hook 是 one-shot（`conv.summary is not None` 后不再触发），与本 hook 触发轮次（第 10 轮+）几乎不重叠。
- **[元数据来源]** → 已决策见 D7：从 `Settings` 环境变量取值（`default_language`/`default_timezone`），`build_adapter_input` 直接读 `get_settings()`，零透传链路；未来 SaaS 化若有 per-client 需求再扩展 HTTP 头作为 override 层（settings 为 fallback）。
- **[time_bucket 分桶规则]** → 已决策见 D9：星期 + 四时段（Morning/Afternoon/Evening/LateNight），28 桶，边界 18:00 归 Evening。

## Resolved Questions

1. **元数据来源** → 见 D7：`language`/`timezone` 从 `Settings` 环境变量取值（`default_language`/`default_timezone`），不透传 HTTP 头。`current_time` 见 D8：取服务器时间。
2. **time_bucket 分桶粒度** → 见 D9：星期 + 四时段（Morning/Afternoon/Evening/LateNight），28 桶，18:00 归 `Evening`。
3. **水位阈值可配置性** → 初版硬编码为模块级常量（如 `AUTO_COMPACT_WATERMARK = 10`），不进 `Settings`；后续按可观测数据（auto-compact 触发频次、cache miss 率、LLM 调用增量）调参后，再视需要提升为 settings 字段。
