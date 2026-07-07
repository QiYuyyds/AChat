# Tasks: P1 补完 — O11 上下文分级、O8 渐进工具、O2 ReAct 剩余步骤

## 1. O11 — DispatchPlanItem 加 context_level 字段

- [x] 1.1 在 `backend/app/schemas/dispatch.py` 的 `DispatchPlanItem` 中新增 advisory 字段 `context_level: Literal["isolated", "standard"] | None = None`（alias `contextLevel`）
- [x] 1.2 确认 `compile_and_validate_dispatch_plan` 不校验 `context_level`（advisory，同 `complexity`）
- [x] 1.3 单元测试：`context_level=None` 默认值、`context_level="standard"` 解析正确、`context_level="full"` 不报错（treated as isolated）

## 2. O11 — build_sub_agent_prompt 分级上下文

- [x] 2.1 在 `backend/app/services/orchestrator_prompts.py` 的 `build_sub_agent_prompt` 中，根据 `task.context_level` 分支：
  - `isolated`/`None`：`recent_limit = SUB_AGENT_CONTEXT_RECENT_LIMIT`（5），`artifact_limit = SUB_AGENT_CONTEXT_RECENT_LIMIT`（5）— 现状不变
  - `standard`：`recent_limit = 10`，`artifact_limit = 10`
- [x] 2.2 `standard` 模式下，recent messages 查询的 `.limit()` 使用 `recent_limit`；existing artifacts 的切片使用 `[:artifact_limit]`
- [x] 2.3 `standard` 模式下，pinned messages 保持全部返回（与 `isolated` 一致，pinned 不受 limit 限制）
- [x] 2.4 单元测试：`isolated` 模式返回 5 条 recent + 5 个 artifact、`standard` 模式返回 10 条 recent + 10 个 artifact、pinned 消息两种模式都全部返回

## 3. O11 — Plan prompt 加 context_level 引导

- [x] 3.1 在 `orchestrator_prompts.py` 的 `ORCHESTRATOR_PLAN_SYSTEM_PROMPT` 中增加 `contextLevel` 字段说明：默认 `isolated` 适合独立执行；审查/调试/跨模块任务建议 `standard`
- [x] 3.2 在 `plan_tasks` 工具的参数说明中加 `contextLevel` 字段描述
- [x] 3.3 单元测试：plan prompt 包含 `contextLevel` 关键词、包含 `standard` 和 `isolated` 说明

## 4. O2 Step 5 — 只读工具调用去重缓存

- [x] 4.1 在 `agent_runner.py` 的 `_run_react_loop` 中，循环顶部初始化 `tool_call_cache: dict[str, Any] = {}`
- [x] 4.2 定义只读工具白名单集合：`READONLY_CACHEABLE_TOOLS = frozenset({"fs_read", "read_artifact", "read_attachment"})`
- [x] 4.3 在工具执行段，当 `tc.name in READONLY_CACHEABLE_TOOLS` 时，构造 cache key `f"{tc.name}:{json.dumps(tc.args, sort_keys=True)}"`
- [x] 4.4 命中缓存时：`result_value = f"[cached] {cached_value}"`，跳过 `execute_with_hooks` 调用，直接构造 `ToolResultEvent`
- [x] 4.5 未命中时：正常执行 `execute_with_hooks`，将结果存入 `tool_call_cache`
- [x] 4.6 有副作用的工具（不在白名单中）不查缓存、不写缓存，直接执行
- [x] 4.7 单元测试：重复 `fs_read` 同路径命中缓存、不同路径不命中、`fs_write` 不缓存、`read_artifact` 缓存命中

## 5. O2 Step 6 — Token 预算控制

- [x] 5.1 在 `_run_react_loop` 的 `for turn` 循环顶部，用 `estimate_tokens(json.dumps(messages))` 估算总 token
- [x] 5.2 从 `model_registry.get_model_limits` 获取 `context_window`；无 model 信息时跳过检查（`model_limit = 0`）
- [x] 5.3 当 `total_tokens > 0.90 * model_limit` 时，调用 `_mid_run_compact(messages)` 进行结构化压缩
- [x] 5.4 实现 `_mid_run_compact(messages)`：调用 `prune_old_tool_results(messages, recent_turns=3)` + `fold_old_messages(messages, fold_threshold=20, keep_recent=15)`，返回压缩后的 messages
- [x] 5.5 当 `total_tokens > 0.95 * model_limit` 时，yield warning `RunUsageEvent`，break 循环
- [x] 5.6 压缩后重新估算 token 并 log（`logger.info`）
- [x] 5.7 单元测试：90% 触发 compact（messages 数量减少）、95% 强制停止、无 model 信息跳过、compact 后 token 下降

## 6. O8 — skill_auto_activator hook

- [x] 6.1 新建 `backend/app/services/hooks/skill_auto_activator.py`，实现 `register(registry: HookRegistry)` 函数
- [x] 6.2 定义规则表 `_MATCH_RULES`：`fs_write` path 后缀匹配（`.py` → `python-best-practices`，`.tsx`/`.jsx` → `frontend-guidelines`）、`bash` command 子串匹配（`pnpm`/`npm` → `frontend-guidelines`，`pytest`/`unittest` → `testing-skill`）
- [x] 6.3 实现 `post_tool_use` handler：检查 `ctx.tool_name` 和 `ctx.args`，匹配规则时返回 `HookResult(action="inject", data=[{"type": "system_hint", "content": "..."}])`
- [x] 6.4 匹配规则大小写不敏感（文件扩展名 `.PY` 同 `.py`）
- [x] 6.5 未匹配时返回 `HookResult(action="allow")`
- [x] 6.6 inject 的 hint 消息格式：`"检测到 {pattern}，可调用 load_skill('{slug}') 获取相关最佳实践指导。"`
- [x] 6.7 在 `hooks/__init__.py` 的 `register_all` 中加 `register_skill_activator(registry)` 调用
- [x] 6.8 单元测试：`fs_write` 写 `.py` 匹配、`bash` 跑 `pnpm` 匹配、`fs_read` 不匹配、大小写不敏感

## 7. O8 — _run_react_loop 支持 inject 消息注入

- [x] 7.1 在 `_run_react_loop` 的 `post_tool_use` hook 派发后，检查返回的 `HookResult`
- [x] 7.2 当 `result.action == "inject"` 时，遍历 `result.data`，将 `{"type": "system_hint"}` 项转为 `{"role": "system", "content": "..."}` 追加到 `messages`
- [x] 7.3 inject 消息不产生 SSE 事件（不 yield），只在 `messages` 列表中对 LLM 可见
- [x] 7.4 单元测试：inject 后 messages 列表多一条 system 消息、不产生额外 SSE 事件

## 8. 集成验证

- [x] 8.1 后端 `ruff check .` 通过（新增/修改文件无 ruff 错误）
- [x] 8.2 后端 `pytest` 通过（新增单元测试全部通过；预存失败不受影响）
- [ ] 8.3 手动验证：群聊 dispatch 的审查任务获得 10 条 recent 上下文（context_level=standard）
- [ ] 8.4 手动验证：ReAct 循环中重复 `fs_read` 同一文件命中缓存（日志可见 `[cached]`）
- [ ] 8.5 手动验证：长 ReAct 循环（>6 轮）触发 mid-run compact（日志可见 token 下降）
- [ ] 8.6 手动验证：`skill_auto_activator` 启用后，写 `.py` 文件触发 skill 建议 inject
- [ ] 8.7 回归测试：CLI agent 路径不受影响（不经过 `_run_react_loop`）
- [ ] 8.8 回归测试：`use_react_loop = False` 回退路径不受影响
