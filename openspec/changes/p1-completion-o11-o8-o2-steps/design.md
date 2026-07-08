# Design: P1 补完 — O11 上下文分级、O8 渐进工具、O2 ReAct 剩余步骤

## Context

P1 变更完成了 ReAct 循环上提（O2 的 5/7 步）和 Hooks 系统（O3），但三项编排优化仍缺失：

1. **O11 上下文分级**：`build_sub_agent_prompt` 固定提供 5 条 recent + 5 个 artifact（`SUB_AGENT_CONTEXT_RECENT_LIMIT=5`），审查/调试任务获得上下文不足。`DispatchPlanItem` 无 `context_level` 字段。
2. **O2 Step 5/6**：`_run_react_loop` 不缓存只读工具结果（LLM 重复调 `fs_read("a.py")` 浪费 token），不检查 messages 总 token（8 轮循环可能超出上下文窗口）。
3. **O8 渐进工具**：skill 加载完全依赖 LLM 主动调 `load_skill`，无基于工具使用模式的自动建议。

现有基础设施：P0 多层压缩（`prune_old_tool_results` + `fold_old_messages` + token 双阈值）、P1 Hooks 系统（10+1 种事件、6 个内置钩子）、P1 `_run_react_loop`（call_once + TurnResult + execute_with_hooks）、skill_service（`list_skills` + `load_skill` 工具）。

约束：不改 DB schema、不改 StreamEvent 协议、不改 CLI adapter 路径、无新增依赖。

## Goals / Non-Goals

**Goals:**

- O11：`build_sub_agent_prompt` 支持 `isolated`（现状）和 `standard`（10 条 + 完整 pinned）两级上下文
- O2 Step 5：`_run_react_loop` 缓存只读工具（`fs_read`/`read_artifact`/`read_attachment`）结果，命中缓存时不重复执行
- O2 Step 6：`_run_react_loop` 每轮检查 messages 总 token，90% 触发 mid-run compact，95% 强制停止
- O8：`post_tool_use` hook 按工具使用模式自动建议加载 skill（inject system 提示消息）

**Non-Goals:**

- 不做 O11 的 `full` 模式（拉完整群聊历史，token 爆炸风险高）
- 不动态添加工具到 `tool_names`（O8 只做建议性 inject，不改变工具集）
- 不缓存有副作用的工具（`fs_write`/`bash`/`write_artifact` 等）
- 不改 CLI adapter 路径
- 不改前端代码

## Decisions

### Decision 1: O11 只做 isolated + standard 两级，不做 full

**选择**: `context_level` 支持 `"isolated"`（默认，现状）和 `"standard"` 两级。不做 `"full"`。

**理由**:
- `full` 模式调 `build_history_for` 拉完整群聊历史，即使有 P0 压缩兜底，对子 agent 来说 token 消耗过大
- `standard`（10 条 recent + 完整 pinned + 10 个 artifact）已覆盖审查/调试任务的需求
- 两级设计向后兼容：`None` 或 `"isolated"` = 现状，无行为变化

**备选方案**: 做 full 模式 → 被否决，token 爆炸风险高，且当前无场景明确需要完整历史。

### Decision 2: O11 的 context_level 是 advisory，不参与校验

**选择**: `compile_and_validate_dispatch_plan` 不校验 `context_level`。`build_sub_agent_prompt` 对 `None`/`"isolated"` 走现有逻辑，仅 `"standard"` 走扩展逻辑。

**理由**:
- 向后兼容：旧 plan 不带 `context_level` 也能通过校验
- 与 P0 的 `complexity` 字段保持一致的 advisory 模式
- 如果 LLM 不填，默认 `isolated` = 现状，无风险

### Decision 3: O2 Step 5 只缓存只读工具，用 args 序列化做 key

**选择**: 维护 `tool_call_cache: dict[str, Any]`，key 为 `"{tool_name}:{json.dumps(args, sort_keys=True)}"`。仅缓存 `fs_read`、`read_artifact`、`read_attachment` 三个只读工具。

**理由**:
- `fs_write`、`bash`、`write_artifact` 等工具有副作用，重复执行可能产生不同结果或造成损害
- `fs_read` 同一文件在同一 run 内内容不变（sandbox 内无外部并发写入）
- `read_artifact`/`read_attachment` 读取的是持久化数据，同一 run 内不变
- `json.dumps(args, sort_keys=True)` 保证参数顺序不影响缓存命中

**缓存规则**:
- 命中缓存：返回 `[cached] {original_result}`，不重复执行，不产生 `tool.result` 事件（直接用缓存值构造）
- 未命中：正常执行，结果存入缓存
- 缓存生命周期：单次 `_run_react_loop` 调用内，run 结束自动释放

**备选方案**: 缓存所有工具 → 被否决，`bash` 命令即使相同也可能有副作用（如 `git status` 在两次调用间文件变了）。

### Decision 4: O2 Step 6 token 估算用 estimate_tokens，不引入 tokenizer

**选择**: 每轮循环顶部用 `estimate_tokens(json.dumps(messages))` 估算总 token，与 `model_registry.get_model_limits` 的 `context_window` 比较。

**理由**:
- `estimate_tokens` 已在 P0 中使用，无需引入新依赖
- 估算精度足够：90%/95% 阈值有 5% 缓冲，不需要精确到 token
- mid-run compact 复用 P0 的 `prune_old_tool_results` + `fold_old_messages`，不调 LLM

**阈值逻辑**:
```python
total_tokens = estimate_tokens(json.dumps(messages))
model_limit = get_model_limits(provider, model_id).context_window

if model_limit > 0:
    if total_tokens > 0.95 * model_limit:
        # 强制停止
        break
    elif total_tokens > 0.90 * model_limit:
        # mid-run compact
        messages = _mid_run_compact(messages)
```

**mid-run compact 逻辑**:
- 调用 `prune_old_tool_results(messages, recent_turns=3)` 裁剪旧 tool_result
- 调用 `fold_old_messages(messages, fold_threshold=20, keep_recent=15)` 折叠旧消息（阈值比正常更激进，因为是在运行中）
- 不调 LLM 摘要（延迟太高）

**备选方案**: 用 tiktoken 精确计数 → 被否决，引入新依赖且 P0 已用 estimate_tokens 建立先例。

### Decision 5: O8 用 inject 提示，不动态添加工具

**选择**: `skill_auto_activator` 的 `post_tool_use` hook 匹配到规则时，返回 `HookResult(action="inject", data=[{"type": "system_hint", "content": "检测到 Python 文件，可调用 load_skill('python-best-practices') 获取最佳实践"}])`。

**理由**:
- `load_skill` 工具已在 agent 有装备 skill 时注入到 `tool_names` 中（现有逻辑）
- 不需要动态修改 `tool_names` 或重建 tools schema
- inject 提示是建议性的，LLM 可以选择不调用
- 避免误激活：只做建议，不强制

**备选方案**: 动态添加 `load_skill` 到 `tool_names` → 被否决，需要每轮重建 tools schema，性能开销大且 `call_once` 接口不支持运行时改 `tool_names`。

### Decision 6: O8 匹配规则基于工具名 + 参数模式，不调 LLM

**选择**: 匹配规则是纯 Python 条件判断，不涉及 LLM 调用。

**规则表**:
| 工具 | 条件 | 建议 Skill |
|---|---|---|
| `fs_write` | path 以 `.py` 结尾 | `python-best-practices` |
| `fs_write` | path 以 `.tsx`/`.jsx` 结尾 | `frontend-guidelines` |
| `bash` | command 包含 `pnpm` 或 `npm` | `frontend-guidelines` |
| `bash` | command 包含 `pytest` 或 `unittest` | `testing-skill` |

**理由**:
- 纯条件判断 = 零延迟、零成本
- 规则保守，只匹配高置信度的模式
- 未来可通过 `on_task_verified` hook 或 LLM hook 扩展

**备选方案**: 用 LLM 判断是否需要加载 skill → 被否决，成本高且增加延迟。

### Decision 7: O8 默认关闭，通过 agent hook_names 启用

**选择**: `skill_auto_activator` 不在 `register_all` 中默认注册。agent 的 `hook_names_list` 需包含 `"skill_auto_activator"` 才启用。

**理由**:
- 自动建议可能对某些用户是噪音
- 与现有钩子（`audit_log`、`tool_approval` 等）的启用模式一致
- 用户可以按 agent 粒度选择启用

## Risks / Trade-offs

- **[O11 standard 模式 token 增加]** → 10 条 recent + 10 个 artifact 比 isolated 多约 2x token；但 P0 多层压缩在读取路径上已兜底
- **[O2 工具去重缓存返回旧数据]** → 只缓存只读工具，且 sandbox 内同一 run 无并发写入；`local` 模式下用户可能手动改文件，但 LLM 会在下一轮 `fs_read` 发现变化（缓存 key 不变但文件已变 → 缓存返回旧内容）→ 接受这个 trade-off，因为同一 run 内 LLM 重复读同一文件是低频场景
- **[O2 token 估算不精确]** → 5% 缓冲区间足够；无 model 信息时回退到不检查（与现状相同）
- **[O8 误建议]** → inject 是建议性的，LLM 可以忽略；默认关闭减少噪音
- **[O8 规则不全]** → 初始规则只覆盖 4 种模式，未来可扩展；`post_tool_use` hook 的 matcher 设计支持增量添加

## Open Questions

1. O2 工具去重缓存在 `local` 模式下是否需要 TTL？→ 不需要，缓存生命周期是单次 `_run_react_loop` 调用，run 结束自动释放
2. O8 的 inject 消息是否需要前端可见？→ 不需要，inject 消息注入到 `messages` 列表（LLM 上下文），不产生 SSE 事件
3. O11 的 `standard` 模式是否需要区分群聊和单聊？→ 不需要，`build_sub_agent_prompt` 只在群聊 dispatch 时调用
