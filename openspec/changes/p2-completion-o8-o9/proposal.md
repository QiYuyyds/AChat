# Proposal: P2 补完 — O8 Skill 自动激活增强与 O9 全链路可视化

## Why

P1 变更完成的 O8（渐进式工具扩展）只实现了 1/3 机制（POST_TOOL_USE hook），且存在两个关键缺陷：① 规则引用的 skill 不存在时，LLM 调 `load_skill` 会报错，浪费工具调用轮次；② 缺少 PRE_RUN 阶段的关键词预加载，用户说"测试"时无法在第一轮就获得 skill 建议。同时，O9（全链路可视化）在四个变更中完全缺失——ReAct 循环已提供 turn 级数据（工具调用、token 用量、耗时），但前端无 turn 级展示，用户无法了解 token 去向和工具调用分布。

## What Changes

### O8: Skill 自动激活增强

- **机制①修复（POST_TOOL_USE）**：hook 在建议前检查 skill 是否存在于 registry 且 `load_skill` 是否在 `tool_names` 中，不满足时静默跳过
- **机制②新增（PRE_RUN）**：`skill_auto_activator` 同一文件内新增 `on_run_start` handler，从最后一条 user 消息提取关键词，匹配到时 inject skill 建议
- **规则表自动推导**：skill 的 `SKILL.md` frontmatter 新增可选字段 `trigger_keywords`（字符串列表），`skill_auto_activator` 启动时从 `list_skills()` 读取所有已注册 skill 的 trigger_keywords 自动构建规则表，不再硬编码
- **机制③不做**：不动态扩展 `tool_names`（破坏 prompt caching、安全边界模糊、收益不成立）

### O9: 全链路可视化

- **后端**：`_run_react_loop` 每轮记录 turn 级数据（turn 序号、input/output tokens、tool_calls 列表、duration_ms），yield 新的 `TurnMetricEvent`
- **StreamEvent 协议**：新增 `turn.metric` 事件类型，复用现有 SSE 通道推送
- **前端 — 消息卡**：agent 消息卡底部展示 turn 级 timeline，每个 turn 一个小气泡（token + 工具 + 耗时），总 token / 总耗时汇总，异常 turn 高亮
- **前端 — 群聊调度卡**：Orchestrator 调度卡上展示每个子任务的 turn 级数据折叠面板

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `lifecycle-hooks`: `skill_auto_activator` 新增 `on_run_start` handler（PRE_RUN 关键词预加载）；hook 在建议前检查 skill 存在性与 `load_skill` 可用性
- `agent-skills`: `SKILL.md` frontmatter 新增可选字段 `trigger_keywords`；`SkillMeta` 新增 `trigger_keywords` 字段；`parse_skill_md` 解析该字段
- `stream-events`: 新增 `turn.metric` 事件类型（TurnMetricEvent）
- `adapters`: `_run_react_loop` 每轮记录并 yield `TurnMetricEvent`
- `frontend`: 消息卡新增 turn 级 timeline 展示组件；调度卡新增 turn 级数据折叠面板

## Impact

### 代码影响

- `backend/app/services/hooks/skill_auto_activator.py` — 重写：规则表从 skill frontmatter 自动推导，新增 `on_run_start` handler，添加 skill 存在性检查
- `backend/app/services/skill_service.py` — `SkillMeta` 加 `trigger_keywords` 字段；`parse_skill_md` 解析 frontmatter 中的 `trigger_keywords`
- `backend/app/services/agent_runner.py` — `_run_react_loop` 每轮记录 turn 数据并 yield `TurnMetricEvent`；`on_run_start` dispatch 捕获 inject 返回值
- `backend/app/schemas/events.py` — 新增 `TurnMetricEvent` 类
- `src/shared/types.ts` — 新增 `TurnMetricEvent` 类型定义
- `src/stores/app-store.ts` — 新增 `turn.metric` 事件 reducer
- `src/components/` — 新增 turn timeline 组件；消息卡集成 turn timeline；调度卡集成 turn 折叠面板

### API 影响

- 无 HTTP API 变更
- SSE 新增 `turn.metric` 事件类型（向后兼容，前端不识别时忽略）
- 无 breaking change

### 依赖影响

- 无新增外部依赖
- 不改 DB schema
- 不改 StreamEvent 已有事件类型（只新增）

### 测试影响

- `skill_auto_activator` 修复后的规则匹配测试（skill 存在/不存在、load_skill 在/不在 tool_names）
- `skill_auto_activator` on_run_start handler 关键词匹配测试
- `skill_service` trigger_keywords 解析测试
- `TurnMetricEvent` 序列化测试
- `_run_react_loop` yield TurnMetricEvent 集成测试
- 前端 turn timeline 组件渲染测试
