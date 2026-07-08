# Tasks: P2 补完 — O8 Skill 自动激活增强与 O9 全链路可视化

## 1. O8 — Skill frontmatter trigger_keywords 字段

- [x] 1.1 在 `backend/app/services/skill_service.py` 的 `SkillMeta` dataclass 中新增 `trigger_keywords: list[str]` 字段（默认空列表）
- [x] 1.2 修改 `parse_skill_md` 函数：解析 frontmatter 中的 `trigger_keywords` 字段，若存在则返回 `(name, description, trigger_keywords)` 三元组；若不存在则返回空列表；若格式非法（非字符串列表）则返回空列表并 log 警告
- [x] 1.3 修改 `list_skills` 函数：在 `SkillMeta` 构造时填入 `trigger_keywords`；截取前 10 项，超出时 log 警告
- [x] 1.4 修改 `save_skill` 函数：确保 `trigger_keywords` 字段不影响 slug 生成和存储逻辑（trigger_keywords 只读不写，上传时原样保留 frontmatter）
- [x] 1.5 单元测试：含 trigger_keywords 的 SKILL.md 解析正确、不含时返回空列表、格式非法时返回空列表、超过 10 项时截取

## 2. O8 — skill_auto_activator 重写：规则自动推导

- [x] 2.1 删除 `skill_auto_activator.py` 中的硬编码 `_MATCH_RULES` 列表和 `_register_rule` / `_match_extension` / `_match_command_substring` 函数
- [x] 2.2 新增 `_build_rule_table() -> dict[str, list[str]]` 函数：调用 `list_skills()`，遍历每个 skill 的 `trigger_keywords`，构建 `{slug: [keyword1, keyword2, ...]}` 映射；跳过 `trigger_keywords` 为空的 skill
- [x] 2.3 在 `register()` 函数中调用 `_build_rule_table()` 构建规则表，存为模块级变量 `_RULE_TABLE`
- [x] 2.4 新增 `_match_tool_against_keywords(tool_name: str, args: dict, keywords: list[str]) -> bool` 函数：对 `fs_write` 检查 path 后缀匹配，对 `bash` 检查 command 子串匹配，其他工具不匹配；匹配大小写不敏感
- [x] 2.5 单元测试：规则表从 mock 的 `list_skills()` 返回值正确构建、trigger_keywords 为空的 skill 被跳过

## 3. O8 — skill_auto_activator：post_tool_use handler 修复

- [x] 3.1 重写 `_post_tool_use` handler：遍历 `_RULE_TABLE`，对每个 `(slug, keywords)` 用 `_match_tool_against_keywords` 检查是否匹配
- [x] 3.2 匹配成功后，检查 slug 是否在 `list_skills()` 返回的 slug 集合中（skill 存在性检查）
- [x] 3.3 检查 `load_skill` 是否在 `ctx.tool_names` 中（load_skill 可用性检查）——需要 `HookContext` 新增 `tool_names` 字段
- [x] 3.4 两步检查任一不满足时返回 `HookResult(action="allow")`；都满足时返回 inject 结果
- [x] 3.5 多个 skill 匹配时，返回第一个匹配的（规则表按 slug 排序，确定性）
- [x] 3.6 单元测试：skill 不存在时跳过、load_skill 不在 tool_names 时跳过、skill 存在且 load_skill 可用时 inject

## 4. O8 — skill_auto_activator：on_run_start handler 新增

- [x] 4.1 新增 `_on_run_start(ctx: HookContext) -> HookResult | None` handler：从 `ctx.messages` 提取最后一条 `role=="user"` 的消息文本
- [x] 4.2 遍历 `_RULE_TABLE`，对每个 `(slug, keywords)` 检查是否有任一 keyword 作为子串出现在 user 消息文本中（case-insensitive）
- [x] 4.3 匹配成功后执行与 `_post_tool_use` 相同的 skill 存在性 + load_skill 可用性检查
- [x] 4.4 返回 `HookResult(action="inject", data=[{"type": "system_hint", "content": "..."}])`
- [x] 4.5 在 `register()` 中同时注册 `POST_TOOL_USE` 和 `ON_RUN_START` 两个 handler（同一文件，同一规则表）
- [x] 4.6 单元测试：user 消息含 keyword 时匹配、不含时不匹配、messages 为空时返回 allow

## 5. O8 — HookContext 新增 tool_names 字段

- [x] 5.1 在 `backend/app/services/hook_registry.py` 的 `HookContext` dataclass 中新增 `tool_names: list[str] | None = None` 字段
- [x] 5.2 在 `_run_react_loop` 的 `on_run_start` 和 `post_tool_use` dispatch 调用中，传入 `tool_names=adapter_input.tool_names`
- [x] 5.3 确认 `post_tool_use` 的 dispatch 由 `execute_with_hooks` 内部发起时也能获取 `tool_names`（通过 `ToolContext.hook_registry` 或额外参数传入）
- [x] 5.4 单元测试：hook handler 能从 `ctx.tool_names` 读取当前 run 的工具列表

## 6. O8 — _run_react_loop 捕获 on_run_start inject 返回值

- [x] 6.1 修改 `_run_react_loop` 中 `on_run_start` dispatch（`agent_runner.py:651-657`）：将返回值存入 `result` 变量
- [x] 6.2 当 `result.action == "inject"` 且 `result.data` 非空时，遍历 data，将 `{"type": "system_hint"}` 项转为 `{"role": "system", "content": "..."}` 追加到 `messages`
- [x] 6.3 此逻辑在第一轮 `call_once` 之前执行，确保 LLM 从第一轮就能看到 skill 建议
- [x] 6.4 单元测试：on_run_start 返回 inject 时 messages 增加 system 消息、返回 allow 时 messages 不变

## 7. O9 — TurnMetricEvent 后端定义

- [x] 7.1 在 `backend/app/schemas/events.py` 中新增 `TurnTokenBreakdown` Pydantic 模型：`input_tokens: int`、`output_tokens: int`、`cache_read_tokens: int`（均用 camelCase alias）
- [x] 7.2 新增 `TurnMetricEvent(BaseEvent)` 类：`type: Literal["turn.metric"]`、`run_id: str`、`turn: int`、`tokens: TurnTokenBreakdown`、`tool_calls: list[str]`、`duration_ms: int`
- [x] 7.3 在 `src/shared/types.ts` 中新增对应的 TypeScript 类型 `TurnMetricEvent`
- [x] 7.4 在 `StreamEvent` 联合类型中新增 `{ type: 'turn.metric'; ... }` 分支
- [x] 7.5 单元测试：TurnMetricEvent 序列化/反序列化正确、camelCase alias 正确

## 8. O9 — _run_react_loop yield TurnMetricEvent

- [x] 8.1 在 `_run_react_loop` 的 `for turn` 循环顶部（token budget check 之前），记录 `turn_start = time.monotonic()`
- [x] 8.2 在 `call_once` 事件消费过程中，记录该轮的 `message.usage` 数据（`input_tokens`、`output_tokens`、`cache_read_tokens`）到临时变量 `turn_usage`
- [x] 8.3 在工具执行完成、`deferred_events` yield 之后、`post_turn` hook 派发之前，构造 `TurnMetricEvent`：`turn=turn+1`（1-based）、`tokens=TurnTokenBreakdown(...)`、`tool_calls=[tc.name for tc in tool_calls]`、`duration_ms=int((time.monotonic() - turn_start) * 1000)`
- [x] 8.4 yield `TurnMetricEvent`
- [x] 8.5 在无 tool_calls 的停止轮（`finish_reason=stop`）也 yield TurnMetricEvent（`tool_calls=[]`）
- [x] 8.6 确认 CLI adapter 的 `stream` 路径不 yield TurnMetricEvent（只有 `_run_react_loop` 产生）
- [x] 8.7 单元测试：每轮 yield 一个 TurnMetricEvent、token 数据正确、duration_ms > 0、tool_calls 列表正确

## 9. O9 — 前端 store reducer

- [x] 9.1 在 `src/stores/app-store.ts` 的 SSE 事件 reducer 中新增 `case 'turn.metric'` 分支
- [x] 9.2 在 run state 中新增 `turnMetrics: Record<number, TurnMetricData>` 字段（key 为 turn 序号）
- [x] 9.3 `turn.metric` 事件到达时，将 payload 存入 `runs[runId].turnMetrics[turn]`
- [x] 9.4 `run.end` 事件到达时，标记 `runs[runId].turnMetricsComplete = true`
- [x] 9.5 单元测试：turn.metric 事件正确更新 store、多个 turn 累积正确

## 10. O9 — 前端 TurnTimeline 组件

- [x] 10.1 新建 `src/components/turn-timeline.tsx` 组件，接收 `turnMetrics: Record<number, TurnMetricData>` 和 `totalTokens` / `totalDuration` props
- [x] 10.2 默认折叠状态：单行显示 `"N turns · X tokens · Ys"`，点击展开/收起
- [x] 10.3 展开状态：横向排列每个 turn 的气泡，每个气泡显示 turn 序号、token 数、工具图标（用 lucide-react 图标）、耗时
- [x] 10.4 异常 turn 高亮：计算平均 duration 和 tokens，超过 2x 平均值的气泡用 amber 色背景
- [x] 10.5 无 turnMetrics 数据时不渲染组件（CLI agent 场景）
- [x] 10.6 单元测试：折叠状态显示摘要、展开状态显示气泡、异常高亮逻辑正确

## 11. O9 — 消息卡集成 TurnTimeline

- [x] 11.1 在消息卡组件中，当 message 关联的 run 有 `turnMetrics` 数据时，在消息底部渲染 `<TurnTimeline>`
- [x] 11.2 从 store 中获取对应 run 的 turnMetrics 数据传入组件
- [x] 11.3 确认 CLI agent 的消息不显示 TurnTimeline（无 turnMetrics 数据，组件自动不渲染）
- [x] 11.4 视觉验证：TurnTimeline 不干扰消息内容阅读，折叠态高度 ≤ 24px

## 12. O9 — 群聊调度卡集成 TurnTimeline

- [x] 12.1 在调度卡组件中，当子任务的 run 有 `turnMetrics` 数据时，新增可折叠面板 "Turn Metrics (N)"
- [x] 12.2 面板默认折叠，点击展开后渲染 `<TurnTimeline>` 组件（复用消息卡的组件）
- [x] 12.3 无 turnMetrics 数据的子任务不显示面板
- [x] 12.4 视觉验证：折叠面板不干扰调度卡的主体信息展示

## 13. 集成验证

- [x] 13.1 后端 `ruff check .` 通过（新增/修改文件无 ruff 错误）
- [x] 13.2 后端 `pytest` 通过（新增单元测试全部通过）
- [x] 13.3 前端 `pnpm typecheck` 通过
- [x] 13.4 前端 `pnpm lint` 通过
- [x] 13.5 手动验证：上传含 `trigger_keywords` 的 skill → agent 运行时 user 消息含关键词 → 第一轮就收到 skill 建议
- [x] 13.6 手动验证：skill 不存在时 hook 静默跳过，LLM 不收到错误建议
- [x] 13.7 手动验证：agent 未装备 skill 时（load_skill 不在 tool_names）hook 静默跳过
- [x] 13.8 手动验证：SDK agent 多轮 run → 消息卡底部显示 turn timeline → 展开后可见每轮 token + 工具 + 耗时
- [x] 13.9 手动验证：异常 turn（耗时过长）高亮显示
- [x] 13.10 手动验证：CLI agent 消息不显示 turn timeline
- [x] 13.11 手动验证：群聊调度卡的子任务可展开 turn metrics 面板
- [x] 13.12 回归测试：现有 `skill_auto_activator` 的旧规则不再硬编码，已有测试需更新为 mock `list_skills`
