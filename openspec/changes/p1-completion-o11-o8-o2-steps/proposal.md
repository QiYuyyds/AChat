# Proposal: P1 补完 — O11 上下文分级、O8 渐进工具、O2 ReAct 剩余步骤

## Why

P1 变更（`p1-react-loop-hooks`）完成了 O2 ReAct 循环上提的 5/7 步和 O3 Hooks 系统，但遗留了三项未实现：1）O11 子 Agent 上下文可调隔离——所有子任务获得相同级别的上下文（5 条 recent + 5 个 artifact），审查任务和调试任务无法获得更多上下文；2）O8 渐进式工具扩展——skill 加载完全依赖 LLM 主动调用，缺少基于工具使用模式的自动激活；3）O2 的 Step 5（重复工具调用去重）和 Step 6（token 预算控制）未实现，长 ReAct 循环中重复只读工具调用浪费 token，且 messages 列表可能超出模型上下文窗口。

## What Changes

### O11: 子 Agent 上下文可调隔离（isolated + standard 两级）

- `DispatchPlanItem` 新增 advisory 字段 `context_level: Literal["isolated", "standard"] | None`（默认 `isolated`，向后兼容）
- `build_sub_agent_prompt` 根据 `context_level` 分级提供上下文：
  - `isolated`（现状）：最近 5 条消息 + 最近 5 个 artifact + upstream artifact 摘要
  - `standard`：最近 10 条消息 + 完整 pinned 消息 + 最近 10 个 artifact + upstream artifact 摘要
- Orchestrator plan prompt 增加 `contextLevel` 字段说明，引导 LLM 为审查/调试任务选择 `standard`
- 不做 `full` 模式（拉取完整群聊历史），避免 token 爆炸风险

### O2 Step 5: 重复工具调用去重（只读工具）

- `_run_react_loop` 中维护 `tool_call_cache: dict[str, Any]`，key 为 `"{tool_name}:{json.dumps(args, sort_keys=True)}"`
- 仅缓存只读工具：`fs_read`、`read_artifact`、`read_attachment`
- 命中缓存时返回 cached result 并标注 `[cached]` 前缀，不重复执行
- 有副作用的工具（`fs_write`、`bash`、`write_artifact` 等）不缓存

### O2 Step 6: Token 预算控制

- `_run_react_loop` 每轮循环顶部估算 `messages` 总 token 数
- 当 token > 90% model_limit 时，触发 mid-run compact（复用 P0 的 `prune_old_tool_results` + `fold_old_messages`）
- 当 token > 95% model_limit 时，强制停止循环并 yield warning event
- 无 model 信息时回退到不检查（与现状相同）

### O8: 渐进式工具扩展（Skill 自动激活）

- 新增 `hooks/skill_auto_activator.py`，注册 `post_tool_use` hook
- hook 检查工具调用结果，按规则匹配 skill：
  - `fs_write` 写了 `.py` 文件 → 建议加载 `python-best-practices`
  - `fs_write` 写了 `.tsx`/`.jsx` 文件 → 建议加载 `frontend-guidelines`
  - `bash` 跑了 `pnpm`/`npm` → 建议加载 `frontend-guidelines`
  - `bash` 跑了 `pytest`/`unittest` → 建议加载 `testing-skill`
- 匹配时通过 `HookResult(action="inject")` 注入一条 system 提示消息，引导 LLM 调用 `load_skill`
- 不动态添加工具到 `tool_names`（`load_skill` 已在 agent 有装备 skill 时注入）
- 默认关闭，通过 agent 的 `hook_names` 配置启用

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `orchestrator`: `DispatchPlanItem` 新增 `context_level` advisory 字段；`build_sub_agent_prompt` 根据 context_level 分级提供上下文
- `lifecycle-hooks`: 新增 `skill_auto_activator` 内置钩子；`HookResult` 的 `inject` 动作在 `post_tool_use` 中支持注入 system 提示消息
- `adapters`: `_run_react_loop` 新增只读工具去重缓存和 token 预算控制逻辑

## Impact

### 代码影响

- `backend/app/schemas/dispatch.py` — `DispatchPlanItem` 新增 `context_level` 字段
- `backend/app/services/orchestrator_prompts.py` — `build_sub_agent_prompt` 加 context_level 分支；plan prompt 加说明
- `backend/app/services/agent_runner.py` — `_run_react_loop` 加工具去重缓存 + token 预算检查
- `backend/app/services/hooks/skill_auto_activator.py` — **新增文件**，post_tool_use hook
- `backend/app/services/hooks/__init__.py` — `register_all` 中加注册

### API 影响

- `plan_tasks` 工具参数新增 `contextLevel`（advisory，不强制），向后兼容
- 无 HTTP API 变更
- 无 breaking change

### 依赖影响

- 无新增外部依赖
- 不改 DB schema
- 不改 StreamEvent 协议

### 测试影响

- `dispatch_plan` 新增 `context_level` 字段校验测试
- `build_sub_agent_prompt` 的 isolated/standard 分级测试
- `_run_react_loop` 工具去重缓存测试（命中/未命中/有副作用工具不缓存）
- `_run_react_loop` token 预算控制测试（90% 触发 compact / 95% 强制停止 / 无 model 信息回退）
- `skill_auto_activator` 匹配规则和 inject 注入测试
