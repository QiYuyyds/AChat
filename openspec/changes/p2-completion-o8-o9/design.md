# Design: P2 补完 — O8 Skill 自动激活增强与 O9 全链路可视化

## Context

P1 变更完成了 O8 的机制①（POST_TOOL_USE hook → `skill_auto_activator.py`）和 O9 的基础设施（ReAct 循环 `_run_react_loop` 提供每轮 turn 数据）。但存在以下问题：

1. **O8 机制①缺陷**：`skill_auto_activator.py` 的 6 条硬编码规则引用了 3 个不存在的 skill（`python-best-practices`、`frontend-guidelines`、`testing-skill`），LLM 跟随建议调 `load_skill` 会收到 "Skill not found" 错误。且当 agent 未装备任何 skill 时，`load_skill` 不在 `tool_names` 中，建议无法执行。
2. **O8 机制②缺失**：用户说"帮我写测试"时，`skill_auto_activator` 无法在 run 开始时就建议 testing-skill，必须等到 LLM 写代码 → bash 跑 pytest 后才触发，延迟 2-3 轮。
3. **O8 规则维护困难**：新增 skill 需要改 `skill_auto_activator.py` 代码，用户上传的 skill 无法声明自己的触发条件。
4. **O9 完全缺失**：`_run_react_loop` 每轮有 `tool_calls`、`usage`（通过 `message.usage` 事件）、turn 编号，但无 `TurnMetricEvent` 将这些数据推送到前端。前端 `RunUsageEvent` 只有 run 级总量。

现有基础设施：P1 Hooks 系统（10 种事件类型、`HookResult` inject 动作）、`_run_react_loop`（turn 级循环）、`skill_service`（`list_skills` + `parse_skill_md` + `read_skill_body`）、`load_skill` 工具（progressive disclosure）。

约束：不改 DB schema、不改已有 StreamEvent 类型（只新增）、不改 CLI adapter 路径、无新增外部依赖。

## Goals / Non-Goals

**Goals:**

- O8 机制①修复：hook 建议前检查 skill 存在性 + `load_skill` 可用性
- O8 机制②新增：`on_run_start` handler 从 user 消息提取关键词，预加载 skill 建议
- O8 规则自动推导：skill 的 `SKILL.md` frontmatter 声明 `trigger_keywords`，hook 启动时自动读取构建规则表
- O9 后端：`_run_react_loop` 每轮 yield `TurnMetricEvent`（turn、tokens、tool_calls、duration_ms）
- O9 前端：消息卡底部 turn timeline 展示；群聊调度卡 turn 级数据折叠面板

**Non-Goals:**

- 不做 O8 机制③（动态扩展 `tool_names`）— 破坏 prompt caching、安全边界模糊
- 不做 Orchestrator 仪表盘页面（token 对比热力图、DAG timeline 可视化）— 范围太大，后续独立变更
- 不改 CLI adapter 路径（CLI 自管循环，不产生 turn 级数据）
- 不做 skill body 自动加载（inject body 而非建议调 load_skill）— 中置信度场景 token 浪费风险
- 不改前端整体布局（只在现有消息卡和调度卡内增加组件）

## Decisions

### Decision 1: 规则表从 skill frontmatter 自动推导，不硬编码

**选择**: `SKILL.md` frontmatter 新增可选字段 `trigger_keywords: list[str]`。`skill_auto_activator` 在 `register()` 时调用 `list_skills()` 读取所有 skill 的 `trigger_keywords`，自动构建规则表。`post_tool_use` 和 `on_run_start` 两个 handler 共享同一规则表。

**理由**:
- 用户新增 skill 时在 `SKILL.md` 里声明触发关键词即可，不需要改代码
- 规则表始终与 registry 中的实际 skill 同步，不会引用不存在的 slug
- `trigger_keywords` 是可选字段——不填的 skill 不会被自动建议，仍可通过 `/` 命令手动加载
- 两个 handler 共享规则表，维护成本低

**frontmatter 示例**:
```yaml
---
name: python-best-practices
description: Python 代码最佳实践指南
trigger_keywords:
  - python
  - pytest
  - .py
  - pip
---
```

**备选方案**: 保持硬编码规则表 → 被否决，每新增 skill 都需要改代码，且当前规则引用了不存在的 skill。

### Decision 2: trigger_keywords 同时服务两种匹配模式

**选择**: 同一 `trigger_keywords` 列表同时用于 `on_run_start`（user 消息关键词匹配）和 `post_tool_use`（工具参数模式匹配），不做区分。

**理由**:
- 关键词如 `python`、`.py`、`pytest` 既能出现在 user 消息中（"帮我写个 Python 脚本"），也能出现在工具参数中（`fs_write` 的 path 以 `.py` 结尾，`bash` 的 command 包含 `pytest`）
- 两种 handler 用不同的匹配函数（消息用子串匹配，工具参数用 path 后缀 / command 子串），但读同一关键词列表
- skill 作者只需在一个地方声明触发条件

**匹配逻辑**:
- `on_run_start`：遍历 trigger_keywords，任一出现在 user 消息文本中（case-insensitive 子串匹配）→ 匹配
- `post_tool_use`：遍历 trigger_keywords，任一出现在 `fs_write` 的 path 后缀或 `bash` 的 command 子串中 → 匹配
- 两种匹配只对 `fs_write`、`bash`、`read_artifact` 等特定工具生效；其他工具不匹配

**备选方案**: 分 `message_keywords` 和 `tool_keywords` 两个字段 → 被否决，增加 skill 作者的认知负担，且大多数关键词两种场景通用。

### Decision 3: skill 存在性 + load_skill 可用性双重检查

**选择**: hook 在返回 inject 建议前执行两步检查：
1. 建议的 slug 在 `list_skills()` 返回的集合中（skill 存在）
2. `load_skill` 在当前 run 的 `tool_names` 中（LLM 能调用）

任一不满足时，hook 返回 `HookResult(action="allow")`，静默跳过。

**理由**:
- 避免建议 LLM 调用不存在的 skill → 浪费工具调用轮次 + 降低 LLM 对后续建议的信任度
- 避免 `load_skill` 不在 `tool_names` 时建议调用 → LLM 收到 "Unknown tool" 错误
- `tool_names` 在 `_run_react_loop` 中不变（不做机制③），检查一次即可

**检查实现**: hook handler 需要访问当前 agent 的 `tool_names`。通过 `HookContext` 传入——在 `_run_react_loop` 的 `on_run_start` 和 `post_tool_use` dispatch 时，`HookContext` 已有 `agent_id`，hook 可从 `agent` 记录获取 `tool_names_list` 和 `skill_names_list`。但为避免 hook 中查 DB，改为在 `HookContext` 新增 `tool_names: list[str] | None` 字段，由 `_run_react_loop` 传入。

**备选方案**: 在 hook 中查 DB 获取 agent 的 tool_names → 被否决，hook 应轻量，不应有 DB 查询。

### Decision 4: on_run_start dispatch 需捕获 inject 返回值

**选择**: `_run_react_loop` 中 `on_run_start` 的 `dispatch` 调用从 fire-and-forget 改为捕获返回值。如果返回 `HookResult(action="inject")`，将 hint 消息追加到 `messages` 列表（在 user 消息之后、第一轮 `call_once` 之前）。

**理由**:
- 当前 `on_run_start` dispatch 的返回值被丢弃（`agent_runner.py:651-657`），inject 动作无法生效
- `post_tool_use` 的 inject 已有先例（`agent_runner.py:863-871`），模式一致
- hint 在第一轮 `call_once` 前注入，LLM 从第一轮就能看到 skill 建议

**备选方案**: 用 `pre_turn` 替代 `on_run_start` → 被否决，`pre_turn` 每轮都触发，但关键词匹配只需在第一轮做一次（user 消息不变）。用 `on_run_start` 更语义化且只触发一次。

### Decision 5: TurnMetricEvent 在 post_turn 之前 yield

**选择**: `_run_react_loop` 每轮在工具执行完成、`deferred_events`（message.usage + message.end）yield 之后、`post_turn` hook 派发之前，yield 一个 `TurnMetricEvent`。事件包含 turn 序号、该轮 input/output tokens（从 `message.usage` 事件累积）、该轮 tool_calls 列表、该轮 duration_ms（从 `call_once` 开始到工具执行完成）。

**TurnMetricEvent 结构**:
```python
class TurnMetricEvent(BaseEvent):
    type: Literal["turn.metric"] = "turn.metric"
    run_id: str = Field(alias="runId")
    turn: int  # 1-based
    tokens: TurnTokenBreakdown  # {input, output, cacheRead}
    tool_calls: list[str]  # tool names, e.g. ["fs_read", "bash"]
    duration_ms: int
```

**理由**:
- 在 `message.usage` yield 之后 → 能拿到该轮的 token 数据
- 在 `post_turn` 之前 → 事件顺序自然（turn 数据 → turn metric → post_turn hook → 下一轮）
- turn 序号 1-based，与 `post_turn` hook 的 `turn_number` 一致
- `duration_ms` 用 `time.monotonic()` 差值计算，不受系统时钟漂移影响

**备选方案**: 复用 `RunUsageEvent` 加 turn 字段 → 被否决，`RunUsageEvent` 是累积值（run 级），turn 级数据是单轮值，语义不同。前端 reducer 也需要不同的处理逻辑。

### Decision 6: 前端 turn timeline 用气泡布局，默认折叠

**选择**: 消息卡底部新增 turn timeline 组件，默认折叠为单行摘要（总 turn 数 + 总 token + 总耗时），点击展开后显示每个 turn 的气泡（token 数 + 工具图标 + 耗时）。异常 turn（耗时 > 平均值 2x 或 token > 平均值 2x）用警告色高亮。

**理由**:
- 默认折叠 → 不干扰正常对话阅读体验
- 气泡布局 → 紧凑，每个 turn 一个小方块，横向排列
- 异常高亮 → 用户快速定位问题 turn（如 LLM 陷入循环、token 暴涨）
- 群聊调度卡的 turn 数据用折叠面板，与子任务结果平级

**备选方案**: 独立仪表盘页面 → 被否决，范围太大且需要路由变更。内联展示更符合 IM 消费习惯。

### Decision 7: on_run_start inject 和 post_tool_use inject 复用同一注入逻辑

**选择**: `_run_react_loop` 中 `on_run_start` 和 `post_tool_use` 的 inject 处理用同一段代码：检查 `HookResult.action == "inject"`，遍历 `data`，将 `{"type": "system_hint"}` 项转为 `{"role": "system", "content": "..."}` 追加到 `messages`。

**理由**:
- `post_tool_use` 的 inject 已有实现（`agent_runner.py:863-871`），模式已验证
- `on_run_start` 的 inject 逻辑完全一致，只是注入时机不同（第一轮前 vs 工具执行后）
- 避免重复代码

## Risks / Trade-offs

- **[trigger_keywords 字段不填]** → hook 静默跳过该 skill，不影响现有行为。已有 skill（如 `frontend-design`）需要用户手动添加 frontmatter 字段才能被自动建议
- **[on_run_start inject 增加 messages 长度]** → 一条 system hint 约 30-50 tokens，在 90% token 预算检查中可忽略
- **[TurnMetricEvent 增加 SSE 流量]** → 每轮 ~100 bytes JSON，8 轮 run 约 800 bytes，可忽略。前端不识别 `turn.metric` 时自动忽略（SSE 事件类型扩展兼容）
- **[duration_ms 精度]** → `time.monotonic()` 精度足够（ms 级），但包含网络延迟（LLM API 响应时间），不代表纯 LLM 推理时间
- **[skill 存在性检查每轮执行]** → `list_skills()` 扫描文件系统，但 skill 数量通常 < 20，且只在 hook 匹配到关键词后才执行（不是每轮无条件执行）

## Open Questions

1. `trigger_keywords` 是否需要限制最大数量？→ 建议限制 10 个，防止 frontmatter 膨胀
2. Turn timeline 组件是否需要支持点击 turn 跳转到对应消息？→ 后续迭代，当前只展示数据不交互
3. 群聊调度卡的 turn 数据是否需要与子任务的 `report_task_result` 关联？→ 不需要，turn 数据是 run 级的，report_task_result 是 task 级的，两者独立
