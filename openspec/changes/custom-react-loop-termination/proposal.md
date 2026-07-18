## Why

Custom（SDK）Agent 的 ReAct 循环默认 `MAX_TURNS = 8`，长任务会在模型尚未完成时被硬截断，且常以静默结束，用户看不到收尾说明。产品目标是对齐 Claude Code / Codex：**主路径由 model-done（本轮无 tool call）结束**，不再用小步数上限当主控；同时用上下文预算收尾管线与行为断路器防止 runaway。

## What Changes

- **移除 Custom 默认硬步数主控**（`MAX_TURNS = 8` / `REACT_LOOP_MAX_TURNS = 8` 作为产品默认上限）；主结束条件改为「模型本轮 0 tool call」。
- **保留可选超高 `max_tool_turns` 保险丝**（默认关闭或等价「无上限」）；命中时走与 token 预算相同的 soft → forced 收尾管线，不当日常主控。
- **统一 Custom 收尾状态机**（与现有 mid-run compact / token 阈值合并，而非并行两套逻辑）：
  - ~90% context：mid-run compact（续命）
  - ~92–93%：soft wrap-up（隐藏注入「请收工」文案；**仍挂全量 tools**）
  - soft 后仍发 tool：forced final（**1 次 `tools=[]`**，面向用户的自然语言总结）
  - ~95%：hard stop，不再开启新的 tool 轮
- **行为断路器**（防删步数后的空转）：
  - 相同 tool 名 + 稳定参数指纹连续 3 次 → 注入换策略/收尾；再犯 → forced
  - 同一 tool 连续执行失败 ≥ 3 次 → 同上
  - mid-run compact 连续失败 ≥ 3 次 → 停止再 compact，转入 soft → forced
- **可观测结束原因**：
  - 内部 `stop_reason` 枚举（日志 / eval）
  - 用户 UI **轻提示**中文原因（非自然 model-done 时）；soft 注入句 **不出现在聊天气泡**
- **范围约束**：
  - **仅 Custom / SDK ReAct 路径**
  - **不改** Claude Code / Codex CLI adapter 的 vendor loop
  - **不改** `run_agent_loop` 编排语义（solo / coordinated / subagent 的工具与 prompt 注入、dispatch/DAG）
- 同步更新相关编号 spec、eval 中写死 `MAX_TURNS = 8` 的规则，以及子 run 非正常结束时写回父 tool result 的可辨识说明（不改编排框架）。

非目标（本变更不做）：
- `/goal` 状态机、独立 verifier 小模型
- soft 轮只读 tool 白名单
- forced 强制 JSON 结构化输出
- 默认恢复「产品级小 max steps」

## Capabilities

### New Capabilities

- `custom-react-loop-termination`：Custom ReAct 循环的终止哲学、预算收尾管线（compact / soft / forced / hard）、行为断路器、可选 `max_tool_turns`、`stop_reason` 与用户轻提示契约。

### Modified Capabilities

- `adapters`：Custom 适配器 / SDK 执行环不再以默认 8 步硬上限作为正常结束条件；须遵守 model-done 主路径与收尾管线（细节见新 capability；本 delta 仅约束 adapter 边界相关要求）。
- `stream-events`：run 结束相关事件须能携带或关联用户可展示的结束原因轻提示（以及内部 stop_reason 字段约定，若事件契约需扩展）。
- `frontend`：非自然结束时展示轻量中文提示；不渲染 soft wrap-up 注入内容。

## Impact

- **后端**：`backend/app/adapters/custom_adapter.py`、`backend/app/services/agent_runner.py`（ReAct / mid-run compact / consume path）、可能的 run 结果 / 事件 schema、`backend/app/observability/eval_rules.py`（写死 MAX_TURNS=8 处）。
- **前端**：run 结束状态 / 消息旁轻提示展示（`src/stores` 或 run 状态组件）；不新增独立 IM 消息类型亦可（可用 run 元数据）。
- **Spec 文档**：`specs/05-adapter-interface.md`（若仍写 MAX_TURNS=8）、必要时 `specs/19-unified-agent-loop.md` 仅交叉引用「Custom 执行环终止」而不改编排语义。
- **API / 事件**：可能扩展 `run.end`（或等价事件）字段：`stopReason` / `stopReasonLabel`；保持 camelCase 前后端兼容。
- **依赖**：无新第三方依赖预期。
- **CLI / 编排**：明确无功能变更。
