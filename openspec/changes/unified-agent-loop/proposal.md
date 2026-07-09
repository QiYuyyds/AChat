# Proposal: Unified Agent Loop (Claude Code Paradigm)

## Why

当前 AChat 的用户体验跟 Claude Code 有本质差异：用户发出一个小任务（比如"写一个 Flask 后端"），Orchestrator 强制注入产物绑定（`expectedOutputs`）、四级验证 gate（report 内容校验 + LLM 裁判 + 产物绑定 + 4 次 retry harness），导致 agent 被反复重试、中间状态全部暴露给用户——而 Claude Code 在同样的场景下只需一个 while-loop 就能写完、自验、自停。

AChat 的设计初衷是"把多 Agent 协作做成 IM 群聊"，但这一目标被当前过度工程化的 Orchestrator 反噬：**单聊场景下用户只想跟一个 agent 对话（像 Claude Code），群聊场景下用户希望协调者也是一个能直接干活的 Claude Code**。本变更将系统简化为一套统一的 Agent Loop 范式，与 Claude Code 行为对齐。

## What Changes

### 新增

- `Conversation.dispatch_mode` 字段（`solo | orchestrated`），默认 `solo`
- `TaskDispatch` 工具：允许协调者在自己的 loop 内启子 agent（类似 Claude Code 的 Agent tool）
- `SoloAgentLoop`：独立的，单聊场景直接走此路径
- `run_agent_loop` 抽象：统一 Solo / Coordinated / Subagent 三种场景的 loop 实现

### 修改

- `agent_runner.execute_run`：入口分发逻辑 —— 判断 `dispatch_mode` 走 Solo 还是 Coordinated
- `orchestrator.py`：删减 ~60% 代码 —— 去除独立的 verification gate、LLM judge、产物绑定 hard gate、4 次 retry harness
- `orchestrator_prompts.py`：删掉第 142 行 MUST 规则（强制 expectedOutputs + verification），改为"按需提供"
- worker prompt：移除 `report_task_result` 工具注入和 structured report 要求，让模型在 loop 内自然自验、自然输出总结

### 移除 (**BREAKING**)

- `report_task_result` 工具：全系统不再需要结构化上报；`end_turn` 时模型的 text 输出即为总结
- `evaluate_task_result_report()` 外部评估函数：worker loop 内的模型早已看过工具结果，不需要第二个 LLM context 再评
- `_evaluate_with_llm()` 裁判调用：不再有跨 LLM 的独立评判
- `expectedOutputs` 强 binding gate：文件落盘即产物，UI 可见性由此驱动，不阻塞 loop 完成
- `MAX_CHILD_TASK_ATTEMARTS=4` harness：跑完就停，不满意用户对群说一句"不行"
- `continuation_context` / `_build_task_continuation_context`：worker 失败后的提示注入不再需要

## Capabilities

### New Capabilities

- `solo-agent-loop`: 单聊场景下的 Claude Code 式 agent loop（end_turn 停，模型自跑自验自停）
- `task-dispatch-tool`: 协调者在自己 loop 内启子 agent 的工具（位置参数：agent_id, task_description）
- `conversation-dispatch-mode`: Conversation 级别的 `solo | orchestrated` 模式 Capabilities

- `orchestrator`: 删减为"带 TaskDispatch 工具的"，不再包含独立验证层
- `sub-agent-worker`: 复用 `run_agent_loop`，不再有特殊注入（report_task_result / acceptanceCriteria 强约束）
- `agent-runner`: 新增 Solo 分发路径，保留 Coordinated（群聊）路径

## Impact

**代码影响范围：**

- `backend/app/services/agent_runner.py`: 入口分发新增 SoloAgent
- `backend/app/services/orchestrator.py`: 删减验证/重试/产物绑定逻辑（保留 TaskDispatch + DAG 调度）
- `backend/app/services/orchestrator_prompts.py`: 删掉 MUST 规则和 verification 引导
- `backend/app/tools/report_task_result.py`: **完全删除**
- `backend/app/services/task_result_report.py`: **完全删除**（无外部调用方）
- `backend/app/schemas/dispatch.py`: 简化 `DispatchPlanItem`（删 expected_outputs / required_commands / required_evidence）
- `backend/app/db/models.py`: Conversation 表新增 `dispatch_mode` 列

**用户体验影响：**

- 单聊：用户发一条消息，agent 直接干活、自跑测试、自然完成、回一条总结
- 群聊：协调者既能自己写代码又能启 subagent；用户可 @某 agent 直接触发其 Solo loop
- 不再有 gate 状态机曝光给用户（plan / retry / evaluate / 再 retry 全部内部消化）

**兼容性影响：**

- DB migration 给 Conversation 表新增列（默认 `solo`，不影响旧数据）
- `report_task_result` 工具的删除是硬破坏：任何依赖此工具的外部 adapter 需要确认

**测试影响：**

- 现有 test_tools.py 中 `test_report_task_result` / `test_plan_tasks_ack` 需删除或改写
- 现有 test_task_result_evaluate.py 整体删除
- 新写 Solo 模式集成测试（端到端：发消息 → agent 跑 → end_turn → 回消息）
