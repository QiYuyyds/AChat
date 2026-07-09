# Design: Unified Agent Loop

## Context

当前 AChat 的 agent 执行路径分两条：

1. **Orchestrator 路径**（群聊 / 多 agent）：`execute_orchestrator_run` → plan → dispatch child runs → 每个 child run 内强制 `report_task_result` + 4 次 retry + LLM judge + 产物绑定 → aggregate
2. **Single agent 路径**（单聊）：`execute_single_run` → 直接 loop，但仍有部分 verification 残留

问题在于：Orchestrator 路径的验证 gate 是**跨 LLM context 的独立层**，不是模型自身行为。Claude Code 的验证是**同一 loop 内模型自驱**（写 → 跑测试 → 看结果 → 决定下一步），不需要外部裁判。

本设计将全系统统一为**一个 `run_agent_loop` 抽象**，通过 `dispatch_mode` 和是否携带 `TaskDispatch` 工具来区分 Solo / Coordinated / Subagent 三种场景。

## Goals / Non-Goals

**Goals:**
- 单聊场景：agent 行为与 Claude Code 对齐（end_turn 即完成，模型自跑自验）
- 群聊场景：协调者既能自己写代码又能启 subagent，subagent 也是同一 loop 范式
- @subagent：直接触发该 agent 的 Solo loop
- 删掉所有跨 LLM context 的 verification gate（report_task_result / LLM judge / 产物绑定 hard gate / 4 次 retry harness）

**Non-Goals:**
- 不改变 IM 前端的消息展示结构（SSE 协议、MessagePart 格式不变）
- 不改变 Agent / Conversation / Workspace 等核心实体的 DB schema（仅 Conversation 新增一列）
- 不删除 DAG 调度能力（群聊协调者仍可按依赖顺序启子 agent）
- 不改变 adapter 层（Claude Code / Custom / Codex 适配器无需修改）

## Decisions

### Decision 1: 统一 loop 入口 —— `run_agent_loop`

**选择：** 一个统一的 async 函数，接受 `agent`, `user_message`, `conversation_id`, `mode` 参数，返回 `RunResult`。

**替代方案：** 保留 `execute_single_run` 和 `execute_orchestrator_run` 两个入口，各自维护。

**理由：** 两个入口已经大量重复（tool execution、stream handling、event publishing）。统一后 Solo / Coordinated / Subagent 只是参数不同，减少维护负担。Claude Code 本身也是同一个 loop 处理所有场景。

### Decision 2: 协调者通过 `TaskDispatch` 工具启子 agent

**选择：** 协调者的 tool list 里多一个 `TaskDispatch` 工具，调用时同步启子 agent loop，等待结果后回填到 messages。

**替代方案 A：** 保留当前 `plan_tasks` 工具 + 外部 DAG 调度器。

**替代方案 B：** 协调者通过 HTTP API 启子 agent（跨进程）。

**理由：** 方案 A 的 DAG 调度器是本次删除对象（它依赖 verification gate）。方案 B 引入不必要的网络开销和状态管理复杂度。方案选择同步函数调用——子 agent loop 在同一进程内运行，结果直接返回，与 Claude Code 的 Agent tool 行为一致。

### Decision 3: 删掉 `report_task_result` 工具

**选择：** 完全移除。worker 模型的 `end_turn` 即为完成信号，模型的 text 输出即为总结。

**替代方案：** 保留但改为 optional，让模型自己决定要不要调。

**理由：** 保留 optional 工具仍会占用 tool token budget，且模型在"该不该调"上产生歧义。Claude Code 没有类似工具，模型自然知道"干完活了就说一声"。删除后 tool list 更干净。

### Decision 4: 产物绑定从 hard gate 改为 soft UI hint

**选择：** 文件落盘后，UI 通过 workspace 文件列表展示产物（类似 VS Code 的 file tree），不再有 `project artifact` 强绑定。

**替代方案：** 保留 project artifact 但改为异步生成（不阻塞 loop 完成）。

**理由：** 当前 project artifact 绑定依赖 `evidence.file_writes`，而 review-mode 写入不记录 evidence，导致绑定失败触发重试。改为 UI 层直接读 workspace 文件更直接，也避免了 review-mode 与 evidence 采集的耦合。

### Decision 5: Conversation 新增 `dispatch_mode` 字段

**选择：** `VARCHAR DEFAULT 'solo'`，可选值 `solo | orchestrated`。

**替代方案 A：** 通过 agent 数量自动推断（单 agent → solo，多 agent → orchestrated）。

**替代方案 B：** 通过 plan_tasks 是否调用来推断。

**理由：** 方案 A 无法覆盖"单 agent 但需要协调"的场景（比如单 agent 需要启 subagent 做专项工作）。方案 B 是运行时推断，增加复杂度。显式字段让用户/系统有明确的控制入口。

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| 模型写完不验证就 end_turn（漏掉 bug） | 在 system prompt 加 soft 引导："完成前建议跑 typecheck / tests"，但不 hard gate |
| 群聊协调者滥用 TaskDispatch（所有事都派给 subagent） | 在协调者 prompt 里明确"自己能干的直接干，只有需要不同能力时才 dispatch" |
| 删掉 retry harness 后 worker 出错无法恢复 | 用户对群说"不行，再来"，协调者重新 dispatch；未来可加可选 retry 配置 |
| DB migration 给 Conversation 加列影响在线服务 | 新列有默认值 `solo`，旧代码不读此列不受影响；读新列的代码用 `getattr(conv, 'dispatch_mode', 'solo')` 兼容 |
| 删掉 report_task_result 影响外部 adapter 调用方 | 发布前 grep 全代码库确认无外部调用方；adapter 层不感知此工具 |

## Migration Plan

**Phase 1 — 新增列 + 新增代码（无破坏）：**
1. DB migration：Conversation 表加 `dispatch_mode` 列
2. 新增 `run_agent_loop` 抽象
3. 新增 `SoloAgentLoop` 路径
4. 新增 `TaskDispatch` 工具
5. 新写集成测试

**Phase 2 — 删减旧代码（灰度）：**
1. `agent_runner.execute_run` 加 `dispatch_mode` 判断，默认走 Solo
2. 删 `report_task_result` 工具
3. 删 `evaluate_task_result_report` / `_evaluate_with_llm` / 4 次 retry harness
4. 删 `expectedOutputs` 强 binding gate
5. 旧测试清理

**Phase 3 — 清理 + 文档：**
1. 删 `task_result_report.py` 模块
2. 简化 `orchestrator.py`（保留 TaskDispatch + DAG 调度）
3. 更新 `orchestrator_prompts.py` MUST 规则
4. 更新 CLAUDE.md 描述新范式

**回滚策略：** Phase 1 可独立回滚（新列有默认值，新代码未被调用）。Phase 2 通过 feature flag 控制（`dispatch_mode` 字段），发现异常立刻切回 `orchestrated`。

## Open Questions

1. **Solo 模式下 agent 能否启 subagent？** 当前设计：不能。Solo 是纯单 agent 体验。如果 agent 需要帮手，提示用户切换到群聊。
2. **群聊协调者是否保留 aggregate 阶段？** 当前设计：保留，但简化为"给用户的自然语言总结"，不再有结构化 XML 输入。
3. **Subagent 的产物如何传回父 loop？** 当前设计：subagent 的 `end_turn` text 作为 TaskDispatch 工具的返回值；文件产物通过 workspace 共享磁盘。
