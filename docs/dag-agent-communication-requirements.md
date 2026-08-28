# 需求变更文档：DAG 子 Agent 横向通信与上下文复用

> **状态**：需求定义（非变更提案）
> **关联 spec**：`specs/19-unified-agent-loop.md`
> **关联代码**：`backend/app/services/dag_executor.py`、`backend/app/services/agent_loop.py`、`backend/app/tools/dispatch_plan.py`、`backend/app/tools/task_dispatch.py`、`backend/app/services/agent_runner.py`、`backend/app/tools/report_result.py`（新增）、`backend/app/services/react_loop_termination.py`

---

## 1. 问题陈述

### 1.1 现状

当前 DAG 多 Agent 调度架构（`dispatch_plan` 工具 + `dag_executor`）存在两个结构性缺陷：

**缺陷 A：子 Agent 之间零横向通信**

DAG 的 `dependsOn` 仅控制**执行顺序**（拓扑波调度），不传递**数据**。`DispatchPlanItem` schema 已定义 `inputs: list[DispatchTaskInput]` 字段（含 `fromTaskId` / `outputId`），但 `dag_executor._execute_node` 完全未读取该字段——子 Agent 仅通过 `task_description`（纯字符串）获得任务描述，看不到上游 Agent 的输出结果。

一个子 Agent 如果发现上游产出缺少关键信息，只能：
- 自己猜测/编造（产出质量下降）
- 返回 `failed`（整个 DAG 波失败传播）
- 返回 partial + summary（主 Agent 收到后决定是否重试，但上游 Agent 的 run 已结束）

**缺陷 B：重试导致 KV Cache 全量失效**

主 Agent 接收到子 Agent 失败信号后，重新调用 `dispatch_plan` 派发新任务。新任务 = 新 `AgentRun` = 新 `RunArgs` = 全新 ReAct loop。即使新任务的 system prompt + task description 与上次完全一致（DeepSeek prefix cache 可命中前缀），子 Agent 上次执行中的工具调用历史（读文件结果、代码生成中间态）全部丢失，无法复用。

### 1.2 影响范围

| 维度 | 影响 |
|------|------|
| 产出质量 | 子 Agent 因看不到上游输出，可能产出不一致或需返工的结果 |
| 算力浪费 | 重试时子 Agent 重新执行已完成的工具调用（文件读取、代码分析等） |
| 延迟 | 主 Agent 全量重规划 + 子 Agent 全量重执行的端到端延迟高 |
| 用户体验 | DAG 复杂任务的一次成功率低，频繁重试导致等待时间长 |

---

## 2. 设计决策

### 2.1 已选方案：D-1 消息注入式

选择 **D-1（消息注入式）** 而非 D-2（真暂停/恢复），原因：

1. **LLM API 无状态**：即使 D-2 实现了真正的 suspend（`await` 挂起），恢复时仍是新 API 请求。DeepSeek prefix cache 的命中条件是 messages 前缀一致，与是否 suspend 无关——只要恢复后的 messages 前缀与之前一致，prefix cache 自然命中
2. **`_run_react_loop` 是 `AsyncIterator[StreamEvent]`**：async generator 不能在中间"暂停"后在另一个 task 里恢复。D-2 需要把 generator 改成可序列化状态机，改动量巨大
3. **Message 表已完整持久化子 Agent 对话**：子 Agent 的所有消息（含 `hidden=True` 的 clone-subagent 消息）已持久化到 `Message` 表（`persist_event` 写入，`_VISIBLE_EVENT_TYPES` 注释明确「persisted to DB but NOT published to SSE bus」）。D-1 直接从 Message 表重建 chat messages，不依赖 checkpoint——数据更完整、一致性更高
4. **与 CLAUDE.md §3.6 兼容**：D-1 不需要把 while-loop 改成可暂停的，子 Agent 仍走 `execute_simple_run`，只是增加 `ask_peer` 工具和上游输出注入

### 2.2 `ask_peer` 工具边界：B+C 混合

`ask_peer` 可向以下两类目标提问/留言：
- **同 DAG 内任意已完成节点**（选项 B）：从 Message 表重建目标 Agent 的完整对话历史（包括 `hidden=True` 的消息），追加问题后创建 mini-run 处理
- **主 Agent**（选项 C）：主 Agent 在 `dispatch_plan` 执行期间处于 `await`，其 ReAct loop 不在运行。`ask_peer` 向主 Agent 留言时，将问题作为待处理消息暂存（mailbox），主 Agent 的 `dispatch_plan` tool_result 返回后，下一轮 ReAct turn 能看到这些留言。此路径是**异步留言**，子 Agent 不期望收到回复（详见 REQ-6 设计定位）

不选择"保持所有子 Agent alive 直到 DAG 结束"（纯选项 B 的原形态），原因：
- 子 Agent 的 `_run_react_loop` 是同步 `await` 的——run 结束就 return，不能"保持存活"
- 强行保持存活需要把 ReAct loop 改成可等待的，滑向 D-2
- 用 Message 表重建对话历史 + mini-run 恢复上下文，效果等价且改动可控——子 Agent 的消息已在 DB 中持久化，直接查询重建即可

### 2.3 与 CLAUDE.md §3.6 的兼容性

| 约束 | 兼容性 |
|------|--------|
| "所有 Agent 走统一 `run_agent_loop`" | ✓ mini-run 仍走 `execute_simple_run` |
| "不要为任何模式写独立服务路径" | ✓ `ask_peer` / `report_result` 是标准工具，走 `tool_registry` 注册 |
| "每次 dispatch = 新 run" | △ mini-run 是新 run，但从 Message 表重建 messages（含 hidden 消息）——复用已有的 `persist_event` + `build_history_for` 内部逻辑，不是新概念 |
| `MAX_DISPATCH_DEPTH = 3` | ✓ `ask_peer` 创建的 mini-run 深度 +1，受同一深度限制 |

### 2.4 上游输出传递方案：方案 H — 终态工具 + 结构化报告（`report_result`）

上游 Agent 的输出如何传递给下游，有五种可选方案（D/E/F/G/H）。经分析比较后选择 **方案 H**。

**方案对比矩阵**：

| 方案 | 额外 API 调用 | token 控制 | 信息完整性 | 延迟增加 | Agent 自主性 |
|------|-------------|-----------|-----------|---------|------------|
| D — LLM 总结后注入 | +1 次（压缩调用） | 可控 | 有损（LLM 可能丢细节） | +2-5s | 被动 |
| E — 普通 report_result 工具 | +1 turn（end_turn） | 可控 | 高（Agent 自定） | +1-2s | 主动 |
| F — checkpoint 直取 | 0 | 不可控 | 完整但无视角 | 0 | 被动 |
| G — 截断 + 描述混合 | 0 | 中等 | 有损（尾部截断） | 0 | 被动 |
| **H — 终态工具 + 结构化报告** | **0（同 turn 内终止）** | **可控** | **高（Agent 自定）** | **0** | **主动** |

**选择方案 H 的理由**：

1. **零额外 API 调用**：方案 H 引入「终态工具」概念——子 Agent 在最后一个 turn 调用 `report_result` 后，ReAct loop 直接终止，不需要额外 `end_turn` turn。与方案 E（普通工具 + 多一个 end_turn turn）相比，省掉一次 LLM 调用
2. **Agent 视角最优**：与方案 D（系统事后总结）和方案 F/G（原始数据截断）相比，由 Agent 自己决定报告什么内容，信息完整性和准确性最高
3. **token 可控**：Agent 在 `report_result` 的 `summary` 字段中自行控制输出长度，系统不需要额外压缩
4. **结构化输出**：`report_result` 返回结构化 payload（summary / key_decisions / files_changed / artifacts），下游注入时格式清晰，且 `NodeResult` 的 `workspace_changes` / `artifact_ids` 可直接从 payload 中取值，自然解决 REQ-2
5. **KV Cache 友好**：终态工具在同 turn 内终止 loop，不产生新 API 请求

**方案 H 的核心机制——终态工具**：

当前 `_run_react_loop` 的终止条件是 `len(tool_calls) == 0`（model 不再调工具时结束）。方案 H 在此基础上新增「终态工具」概念：某些工具（目前仅 `report_result`）被调用后，loop 在执行完该工具后直接终止，不再进入下一轮 model call。

```
正常 ReAct loop 终止路径（现有）:
  turn N: model → [0 tool calls] → 终止

方案 H 终态工具终止路径（新增）:
  turn N: model → [1 tool call: report_result(...)]
          → 执行 report_result handler（存储结构化结果）
          → 检测到终态工具 → 设置 stop_reason = COMPLETE
          → 终止 loop（不再进入 turn N+1）
```

**设计合理性**：
- `end_turn` 本身是 model 的决定——调 `report_result` 也是 model 的决定，本质等价
- 当前 `len(tool_calls) == 0` 终止条件表达的是「model 不想再调工具了」——调 `report_result` 表达的是「我完成了，这是我的结构化结果」
- 终态工具不影响现有终止逻辑的语义——`len(tool_calls) == 0` 路径完全保留，终态工具是在 `len(tool_calls) > 0` 分支中新增的检查

**非 subagent 模式不注入 `report_result`**：solo / coordinated 模式的 Agent 直接面向用户，自然语言回复 + `end_turn` 是合理的终止方式。`report_result` 仅注入 subagent 模式。

---

## 3. 需求清单

### 3.0 `report_result` 终态工具与 ReAct loop 终态终止（前置需求）

**REQ-0a：新增 `report_result` 工具**

子 Agent 在完成任务时，通过 `report_result` 工具输出结构化结果。该工具是**终态工具**——调用后 ReAct loop 直接终止，不再进入下一轮 model call。

- **工具名**：`report_result`
- **参数**：

```json
{
  "type": "object",
  "required": ["summary"],
  "properties": {
    "summary": {
      "type": "string",
      "description": "任务完成的摘要。面向下游 Agent 或主 Agent，需自包含关键结论和产出说明。控制在 500 token 以内。"
    },
    "keyDecisions": {
      "type": "array",
      "items": {"type": "string"},
      "description": "关键决策或发现列表（可选）。"
    },
    "filesChanged": {
      "type": "array",
      "items": {"type": "string"},
      "description": "新增或修改的文件路径列表（可选）。"
    },
    "artifacts": {
      "type": "array",
      "items": {"type": "string"},
      "description": "产出的 artifact ID 列表（可选）。"
    }
  }
}
```

- **handler 行为**：
  1. 将参数组装为 `ReportResultPayload`，存储到进程内 run-level 缓存（`_report_result_cache`，key = `run_id`）
  2. 返回 `ok({"status": "reported"})`——但由于终态终止，LLM 不会看到此返回
  3. 不产生 tool_result part 到消息（终态终止后不需要 LLM 再处理）

- **注册位置**：`backend/app/tools/registry.py` 全局注册
- **注入条件**：`_run_subagent_loop` 中，始终注入（不限 depth——即使 depth 达上限，子 Agent 仍需 `report_result` 来输出结构化结果）

**REQ-0b：ReAct loop 终态工具终止逻辑**

在 `_run_react_loop`（`agent_runner.py`）的工具执行后，新增终态工具检查：

- **`TERMINAL_TOOLS` 常量**：`frozenset({"report_result"})`——终态工具集合，可扩展
- **检查时机**：在 `_run_react_loop` 的 `len(tool_calls) > 0` 分支中，**所有工具执行完毕且 tool_result 消息追加完成后**。具体位置：`asyncio.gather` 执行工具 → 逐个追加 `res.tool_message` 到 `messages` → `term.record_tool_calls()` 后
- **检查逻辑**：如果本 turn 执行的工具调用中包含 `TERMINAL_TOOLS` 中的工具，则：
  1. 此时同 turn 内所有工具（包括非终态工具）的 `tool_result` **已经被追加到 `messages`**——这是设计意图，让 checkpoint / retry 重建时上下文更完整
  2. 设置 `stop_reason = StopReason.COMPLETE`
  3. 发射 `POST_TURN` hook + `ON_STOP` hook
  4. `break`——终止 while loop（不再进入下一轮 model call，LLM 不会再看到这些 tool_result）

```
_run_react_loop 工具执行后新增逻辑:

  if len(tool_calls) == 0:
      # 现有路径：自然终止
      break

  # 执行工具...
  executable = [tc for tc in tool_calls if tc.id not in pre_resolved]
  # ... asyncio.gather 执行工具 ...
  # ... 逐个追加 res.tool_message 到 messages ...
  # ... term.record_tool_calls(exec_names, exec_fps, exec_errors) ...

  # 新增：终态工具检查（在所有 tool_result 追加完成后）
  terminal_calls = [tc for tc in tool_calls if tc.name in TERMINAL_TOOLS]
  if terminal_calls:
      # 终态工具被调用 → 直接终止 loop
      # 注意：同 turn 内所有 tool_result 已追加到 messages（checkpoint 重建更完整）
      # 但 LLM 不会再看到它们（loop 终止，不进入下一轮 model call）
      stop_reason = StopReason.COMPLETE
      # 发射 POST_TURN / ON_STOP hooks
      break
```

- **影响范围**：仅 `_run_react_loop` 的 `len(tool_calls) > 0` 分支，`len(tool_calls) == 0` 路径完全不变
- **`force_final` 交互**：如果 `force_final=True`（上下文预算耗尽时的强制收尾），model 不应调工具（`adapter_input.tool_names = []`），因此终态工具检查不会在 force_final 路径触发

**REQ-0c：`LoopRunResult` 提取结构化结果**

`spawn_subagent_loop`（`agent_loop.py`）在子 Agent run 完成后，优先从 `_report_result_cache` 提取结构化结果：

1. 检查 `_report_result_cache.get(run_id)`——如果有 `ReportResultPayload`，直接使用
2. 如果没有（Agent 未调 `report_result` 就结束了），回退到现有的 `_extract_run_final_text`（取最后一条消息文本）作为 `summary`，`workspace_changes` / `artifact_ids` / `key_decisions` 为空
3. 从 `_report_result_cache` 取完后清理缓存（防止内存泄漏）

**REQ-0d：`_SUBAGENT_SUFFIX` 追加 `report_result` 使用指导**

子 Agent system prompt 追加：

```
### 完成任务时必须调用 report_result
当你完成任务时，**不要直接结束回复**，而是调用 `report_result` 工具提交结构化结果。
- `summary`：面向下游 Agent 或主 Agent 的摘要，需包含关键结论和产出说明
- `filesChanged`：你新增或修改的文件路径列表
- `artifacts`：你产出的 artifact ID 列表（如有）
- `keyDecisions`：关键决策或发现（如有）
调用 `report_result` 后系统会自动结束你的执行，不需要再写"我完成了"之类的文本。

**重要**：调用 `report_result` 时不要在同一轮中同时调用其他工具。`report_result` 是终态工具，
调用后系统会立即终止你的执行——同一轮中的其他工具虽然会执行，但你不会看到它们的结果。
请先完成所有需要的工具调用（如 fs_write / bash），在最后一轮单独调用 `report_result`。
```

### 3.1 DAG Edge 数据传递（上游 → 下游）

**REQ-1：上游输出自动注入**

当 DAG 节点 `t2` 依赖 `t1`（`dependsOn: ["t1"]`），且 `t1` 成功完成时，`t2` 的子 Agent 在启动时自动收到 `t1` 的结构化输出。

- 数据来源：上游 `NodeResult.summary`（来自 `report_result` 的 `summary` 字段，REQ-0c）+ `key_decisions`（如有）
- 注入方式：在 `_execute_node` 调用 `spawn_subagent_loop` 前，将上游结构化输出拼接到 `task_description` 尾部
- 注入格式模板：

```
[原始 task_description]

---
## 上游任务输出

### 任务 {task_id} 的结果
- **摘要**: {summary}
- **关键决策**: 
  - {key_decisions[0]}
  - {key_decisions[1]}
  ...
- **变更文件**: 
  - {files_changed[0]}
  - {files_changed[1]}
  ...
- **产出 Artifact**: 
  - {artifacts[0]}
  ...
```

  字段为空时省略对应小节（如无 `key_decisions` 则不显示"关键决策"行）
- 多上游：当 `t3` 依赖 `t1` 和 `t2` 时，两者输出按 `dependsOn` 顺序拼接，每个上游一个 `### 任务 {task_id} 的结果` 小节
- 失败上游：已标记 `skipped` 的上游不注入（当前架构已跳过下游执行，无需改变）
- 兜底：如果上游 Agent 未调 `report_result`（REQ-0c 回退路径），则注入 `_extract_run_final_text` 提取的自然语言 summary，`key_decisions` / `files_changed` / `artifacts` 为空
- **token 预算控制**：注入前估算上游输出总 token 数。如果上游有 3+ 个节点且每个 summary 接近 500 token，注入可能超过 1500 token。当注入总 token 超过阈值（建议 2000 token）时，对 `summary` 字段做尾部截断并追加 `[summary truncated, {N} tokens omitted]`。`files_changed` / `artifacts` 列表不截断（通常较短）

**REQ-2：上游 Workspace 变更提示**

当 DAG 节点使用 worktree 隔离时，上游 worktree merge back 后的文件变更可被下游感知。但下游子 Agent 不知道上游改了哪些文件。

- 数据来源：上游 Agent 通过 `report_result` 的 `filesChanged` 字段自报告（REQ-0a），存储到 `NodeResult.workspace_changes`
- 注入到下游 task_description 时，额外提供文件变更清单
- 当前 worktree merge_back 机制已确保文件层面可见——此需求只是让下游 Agent 知道**该看哪些文件**
- 兜底：如果上游 Agent 未调 `report_result`，`workspace_changes` 为空列表，下游 Agent 需自行用 `fs_list` / `fs_grep` 探索

**REQ-3：`DispatchPlanItem.inputs` 字段实现**

schema 已定义 `inputs: list[DispatchTaskInput]`，含 `from_task_id` / `output_id` / `description`。当前 `dag_executor` 未读取此字段。

- 在 `_execute_node` 中读取 `task.inputs`
- 对每个 `DispatchTaskInput`，从 `results[inputs.from_task_id]` 获取上游 `NodeResult`
- 将 `inputs.description` + 上游结构化输出（summary / key_decisions / workspace_changes / artifacts）组装为结构化上下文
- 如果 `task.inputs` 为 None，回退到从 `task.depends_on` 推导上游列表（行为与 REQ-1 一致）

### 3.2 `ask_peer` 工具（下游 → 上游 / 主 Agent）

**REQ-4：新增 `ask_peer` 工具**

子 Agent 在执行过程中，可通过 `ask_peer` 工具向同 DAG 内的其他节点或主 Agent 提问。

- **工具名**：`ask_peer`
- **参数**：
  - `peerTaskId`（string, optional）：同 DAG 内的目标节点 ID。省略时向主 Agent 异步留言（不期望收到回复）
  - `question`（string, required）：提问内容
- **返回**：`{ status: "answered" | "pending" | "unavailable" | "limit_reached", answer?: string, note?: string }`
  - `answered`：有 `peerTaskId` 且 mini-run 成功完成，`answer` 为回答文本
  - `pending`：无 `peerTaskId`（向主 Agent 异步留言），`note` 为提示文案
  - `unavailable`：目标 session 不存在或已过期
  - `limit_reached`：同一 `peerTaskId` 的 `ask_count` 达上限
- **注入条件**：
  - 仅在 subagent 模式（`dispatch_mode == "subagent"`）且 `dispatch_depth < MAX_DISPATCH_DEPTH` 时注入
  - solo 模式不注入（无 DAG 上下文）
  - coordinated 模式不注入（主 Agent 本身就是协调者）

**REQ-5：mini-run 创建（向已完成节点提问）**

当 `ask_peer` 指定 `peerTaskId` 时：

1. 从 `AgentSessionRegistry` 查找该 task_id 对应的 `AgentSession`（含 `run_id`、`agent_id`、`conversation_id`、`dispatch_depth`）——目标子 Agent 的 run_id 如 R1，其 `agent_id` 用于创建 mini-run 的 `RunArgs.agent_id`
2. 从 Message 表查询 R1 的完整对话历史：`SELECT * FROM messages WHERE run_id=R1 AND status='complete' ORDER BY created_at`——**包括 `hidden=True` 的消息**（`build_history_for` 默认过滤 `hidden=True`，此处需不过滤）
3. 用 `_messages_to_chat_messages`（从 parts 重建 OpenAI chat messages 格式）将 Message 实体的 parts_list 转换为 chat messages——复用 `build_history_for` 内部的 parts→messages 转换逻辑，但跳过 hidden 过滤
4. **复用目标 Agent 的 system_prompt**：mini-run 仍走 `execute_simple_run` → `build_adapter_input` 标准路径（接受冗余开销，不加 skip flag），但 `build_adapter_input` 产出的 `system_prompt` 在 `_run_react_loop` 中被覆盖——从 `AgentSession` 缓存或 R1 checkpoint 取 R1 的 system_prompt 快照，确保 messages 前缀完全一致，最大化 DeepSeek prefix cache 命中率（详见下方「system_prompt 复用策略」）
5. 创建 mini-run（`run_with_args`），关键 `RunArgs` 字段设置：
   - `override_prompt`：问题文本
   - `dispatch_visibility="hidden"`（mini-run 消息不发布到 SSE，不污染对话历史）
   - `dispatch_depth` = 调用者 depth + 1（受 `MAX_DISPATCH_DEPTH` 限制）
   - `trigger_message_id` = 调用者（子 Agent）的 `trigger_message_id`（mini-run 不是用户消息触发的，复用父链的 trigger；`AgentRun` 表的外键约束允许非用户消息的 trigger）
   - `override_tool_names`：mini-run 仅需回答问题，不需要文件写入/命令执行等工具。设为 `["report_result"]`（仅保留终态报告工具，防止 mini-run 无限派发）。如目标 Agent 是 SDK 路线，baseline 工具（`fs_read` / `fs_list` / `fs_grep` 等只读工具）仍由 `execute_simple_run` 自动合并——这是期望行为，mini-run 可能需要读文件来回答问题
   - `override_system_prompt`：R1 的 system_prompt（从 `AgentSession.system_prompt` 缓存或 checkpoint 取），覆盖 `build_adapter_input` 产出的轻量 system_prompt
   - `override_messages`：`[override_system_prompt] + 重建的 chat messages + [user: question]`，覆盖 `build_adapter_input` 产出的 `messages`（`override_prompt` 非空时为 `None`）

> **注意**：`override_prompt` 非空时，`build_adapter_input` 内部会跳过 `build_history_for`（第 3386 行 `if is_sdk and not args.override_prompt`）和 PromptAssembler（第 3430 行 `if assembler and not args.override_prompt`）——这意味着 mini-run 的 `build_adapter_input` 调用**本就是轻量的**（只做 ModelProfile 解析 + system_prompt 基础构造），不存在冗余的 history 查询和 RAG 检索开销。决策点 2 中"接受冗余开销"的描述应据此修正为"冗余开销实际上不存在"。
6. mini-run 走 `execute_run` → `build_adapter_input` 标准路径（不加 skip flag）。由于 `override_prompt` 非空，`build_adapter_input` 跳过 history / PromptAssembler，产出 `adapter_input.messages = None` 和轻量 `system_prompt`。在 `build_adapter_input` 返回后、`_run_react_loop` 调用前，新增注入点覆盖：`adapter_input.system_prompt` = 优先级取到的 R1 system_prompt，`adapter_input.messages` = `[system_prompt] + 重建的 chat messages + [user: question]`——`_run_react_loop` 检测到 `adapter_input.messages is not None` 时直接使用，不走默认的 system+history+user 组装路径
7. 等待 mini-run 完成，提取回答文本返回给调用者
8. mini-run 的消息 `hidden=True`（由 `dispatch_visibility="hidden"` 控制，`consume_stream` 中 `hidden=True` 时不发布 visible event types 到 SSE）

**数据流示意**：
```
R1 (t1 的 run，已完成):
  Message 表: [msg1(user), msg2(agent, hidden), msg3(agent, hidden), ...]
  checkpoint: messages_json = [sys, user, asst, tool_result, ..., asst(final)]

ask_peer(peerTaskId="t1", question="..."):
  1. 查 AgentSessionRegistry → run_id=R1
  2. 从 Message 表查 R1 的所有消息（含 hidden）
  3. parts → chat messages 重建
  4. build_adapter_input 正常执行（ModelProfile 解析等必需），产出后被覆盖
  5. system_prompt 从 AgentSession 缓存或 checkpoint 取，覆盖 adapter_input.system_prompt
  6. adapter_input.messages 被覆盖为:
     messages = [sys(R1的), user, asst, tool_result, ..., asst(final), user: question]
  7. _run_react_loop → call_once → LLM 处理
  8. KV cache: messages 前缀 == R1 最后 API 请求的 messages → prefix cache 命中 ✓
```

**system_prompt 复用策略**：mini-run 仍走 `execute_simple_run` → `build_adapter_input` 标准路径（**不加 `skip_build_adapter` flag**）。关键发现：`override_prompt` 非空时，`build_adapter_input` 内部**已自动跳过** `build_history_for`（第 3386 行 `if is_sdk and not args.override_prompt`）和 PromptAssembler（第 3430 行 `if assembler and not args.override_prompt`）——因此 mini-run 的 `build_adapter_input` 调用本就是轻量的（只做 ModelProfile 解析 + `agent.system_prompt` + workspace context 组装），**不存在冗余的 history 查询和 RAG 检索开销**。`build_adapter_input` 产出的 `system_prompt` 和 `messages` 在 `_run_react_loop` 中被覆盖：
- system_prompt 来源优先级（覆盖 `adapter_input.system_prompt`）：
  1. `AgentSession` 中缓存的 `system_prompt`（R1 执行时 `build_adapter_input` 产出的完整 system_prompt 含 RAG / dynamic_prefix，存入 `AgentSession`）
  2. 回退：从 R1 最后一个 checkpoint 的 `messages_json[0]` 取（如果 checkpoint 存在——注意 checkpoint 存的是 messages 列表，`messages[0]` 即 system message）
  3. 再回退：不覆盖，使用 `build_adapter_input` 刚产出的 system_prompt（只含 `agent.system_prompt` + workspace context，缺少 RAG / dynamic_prefix，KV cache 命中率降低，但功能正常）
- messages 覆盖：`adapter_input.messages` 被替换为 `[system_prompt] + 重建的 chat messages + [user: question]`——由于 `build_adapter_input` 在 `override_prompt` 非空时产出 `messages = None`（跳过了 history 组装），覆盖时机实际上是在 `execute_run` 的 checkpoint 恢复逻辑**之后**、`_run_react_loop` **之前**通过新增的注入点完成

> **决策**：不引入 `skip_build_adapter` flag。理由：(1) `build_adapter_input` 中的 ModelProfile 解析是 mini-run 必需的（需要知道用哪个 provider / api_key）；(2) `override_prompt` 非空时 `build_history_for` 和 PromptAssembler **本就被跳过**，不存在冗余开销；(3) 加 flag 会导致 `execute_simple_run` 分叉两条路径，维护成本上升不划算。

**REQ-6：主 Agent 异步留言通道（向主 Agent 反馈）**

当 `ask_peer` 不指定 `peerTaskId` 时，此路径是**异步留言机制**，而非同步 Q&A——子 Agent 向主 Agent 反馈问题或建议，主 Agent 在 DAG 执行结束后看到留言，用于后续策略决策（retry / 调整 / 接受）。子 Agent **不期望收到回复**。

1. 将问题暂存到 `AgentSessionRegistry` 的主 Agent mailbox（`dict[parent_run_id, list[str]]`，通过 `ctx.parent_run_id` 获取 key）
2. `ask_peer` 工具返回 `{ status: "pending", note: "反馈已提交给主 Agent，主 Agent 将在 DAG 结束后查看" }`
3. 主 Agent 的 `dispatch_plan` tool_result 返回时，附带上未处理的 mailbox 消息
4. 主 Agent 在下一轮 ReAct turn 中看到留言，决定是否 retry 失败节点、调整后续 DAG、或接受当前结果

> **设计定位**：此路径不是同步 Q&A——主 Agent 在 `await execute_dag()` 期间无法响应，子 Agent 的 run 在节点完成后即结束，**永远收不到回复**。此路径的价值在于：子 Agent 可以将执行中发现的阻塞问题、需要主 Agent 决策的事项以结构化方式传递给主 Agent，主 Agent 在 DAG 结束后集中处理。如果子 Agent 需要同步回复才能继续工作，应走 REQ-5 的 mini-run 路径（向已完成的上游节点提问）。

**REQ-7：防环与深度控制**

- `ask_peer` 创建的 mini-run `dispatch_depth + 1`，受 `MAX_DISPATCH_DEPTH = 3` 限制
- 同一 `peerTaskId` 的 `ask_peer` 调用次数上限：3 次（防止无限追问循环）
- mini-run 内部的 `ask_peer` 不再注入（depth 达上限时自动过滤）
- 主 Agent mailbox 消息不创建 mini-run，不受深度限制

### 3.3 AgentSessionRegistry（会话追踪）

**REQ-8：新增 AgentSessionRegistry**

当前 `_active_runs` 只追踪 `run_id -> (task, cancel_event)`，不追踪 DAG 内的 task_id 与 run_id 映射。

- 新增 `AgentSessionRegistry`（进程内单例，类似 `pending_dispatch_plans`）
- 注册时机：`dag_executor._execute_node` 在 `on_start` 回调中注册 `task_id -> (run_id, agent_id, conversation_id)`
- 注销时机：DAG 执行结束后批量注销该 DAG 的所有 session
- `ask_peer` 通过 task_id 查找目标 run_id，进而加载 checkpoint
- 数据结构：`dict[task_id, AgentSession]`，`AgentSession` 含 `run_id` / `agent_id` / `conversation_id` / `parent_run_id` / `dispatch_depth` / `ask_count`

**REQ-9：DAG 执行上下文传递**

`DagExecContext` 需携带 DAG 级别的元信息，使 `ask_peer` 工具能访问当前 DAG 上下文：

- `DagExecContext` 新增 `dag_id: str` 字段（唯一标识本次 dispatch_plan 调用）
- `DagExecContext` 新增 `all_task_ids: list[str]` 字段（DAG 内所有 task_id 列表）
- `ToolContext` 新增 `dag_id: str | None` 字段（`_execute_node` 创建 ToolContext 时注入）
- `ask_peer` 工具通过 `ctx.dag_id` 从 `AgentSessionRegistry` 查找可用 peer

### 3.4 局部重试优化（主 Agent 侧）

**REQ-10：DAG 结果结构化返回**

`dispatch_plan` 工具返回给主 Agent 的结果需增强，支持局部重试决策：

- 当前返回：`{ tasks: { <id>: { status, summary } } }`
- 增强返回：每个 task 附带 `workspace_changes` / `artifact_ids` / `key_decisions`（来自 `report_result` 的结构化 payload，REQ-0c）
- 失败 task 附带 `error_detail`（子 Agent 的失败原因摘要，来自 `LoopRunResult.text`）
- 主 Agent 可据此决定：重试失败节点、跳过失败节点、或调整后续 DAG
- 兜底：如果子 Agent 未调 `report_result`，`workspace_changes` / `artifact_ids` / `key_decisions` 为空列表，`summary` 回退为 `_extract_run_final_text` 的自然语言文本

**REQ-11：增量 DAG 派发**

主 Agent 不需要全量重新 `dispatch_plan`，可以只派发失败节点的替代任务：

- 新增 `dispatch_plan` 参数 `mode: "full" | "retry"`（默认 `"full"`）
- `mode="retry"` 时，task_id 如果与已完成节点 ID 相同，视为重试——系统从 Message 表重建上次对话历史（含 hidden 消息）+ 复用 system_prompt，追加 retry 指令作为新 user message
- retry 模式下，`dependsOn` 可引用原 DAG 中已成功完成的节点 ID

> **注意**：此需求依赖 `AgentSessionRegistry` 保留已完成节点的 session 信息（至少保留 run_id 和 system_prompt 缓存）。DAG 执行结束后，session 应标记为 `completed` 但保留 N 分钟（可配置，默认 300 秒）供 retry 使用。Message 表数据是持久化的，不受 session 过期影响——session 过期后 retry 仍可从 Message 表重建，只是 system_prompt 需走 `build_adapter_input` 重新构造（cache 命中率降低）。

**retry 的 `run_id` 来源**（session 过期后的反查路径）：
1. **session 未过期**：从 `AgentSessionRegistry` 直接获取 `AgentSession.run_id` 和 `AgentSession.system_prompt`
2. **session 已过期**：从 `AgentRun` 表反查——`SELECT id FROM agent_runs WHERE conversation_id=X AND agent_id=Y ORDER BY created_at DESC LIMIT 1`，取最新的已完成 run。注意这只能找到该 agent 最近的一次 run，不一定是原 DAG 中该 task_id 对应的 run。如果主 Agent 在 retry 时能提供 `originalTaskId` → `originalRunId` 映射（从原 DAG 的 `dispatch_plan` 返回值中获得），则可直接定位
3. **推荐方案**：`dispatch_plan(mode="retry")` 新增可选参数 `originalDagId: str`——主 Agent 从上次 `dispatch_plan` 返回值中获取原 DAG ID，retry 时传入，系统通过 `AgentSessionRegistry` 按 `(originalDagId, task_id)` 查找原 session。session 过期时从 `AgentRun` 表反查 run_id

**retry 时的上下文压缩**（遵循新三层压缩架构，详见 `docs/上下文压缩架构设计文档.md`）：
- 重建历史后，调用 `estimate_messages_tokens`（Layer 1 dict 格式）或 `estimate_dict_message_tokens`（Layer 3 / CompactMessage 格式）估算总 token 数
- 计算比率 `ratio = total_tokens / model_limit`（`model_limit` 从 `get_model_limits(provider, model_id).effective_context_window` 获取，固定 200k）
- 如果 `ratio ≥ COMPACT_MASK_RATIO (0.75)`，调用 `should_compact(state, ratio)` 决定裁剪动作（返回 `"mask"` / `"fold"` / `None`），再调用 `run_compact_pipeline_unified(messages, stage=1|3)` 执行裁剪
  - stage 1 (mask)：通用 mask 窗口外的 Observation，保留 Discourse + Action
  - stage 3 (fold)：折叠旧 turns 为单个 marker
  - `should_compact` 的防横跳规则（最小间隔 K+1 轮 / 直接升级 / fold 后不再压缩）确保不会在阈值附近反复横跳
- 如果 `ratio ≥ SOFT_RATIO (0.93)`，先 mask/fold 后仍超限，追加 `SOFT_WRAPUP_INSTRUCTION` 软收尾指令
- 如果 `ratio ≥ HARD_RATIO (0.95)`，追加 `FORCED_FINAL_INSTRUCTION` 强制总结指令
- 压缩后再追加 retry 指令作为新 user message
- 这防止了"上次因 context 爆炸失败 → retry 重建同样历史 → 再次爆炸"的死循环
- 注意：retry 重建的 messages 应转为 `CompactMessage` 格式（通过 `to_compact_messages_orm`），使用 `run_compact_pipeline_unified` 而非旧的 `run_compact_pipeline`（后者是 dict 格式入口，仅用于 ReAct loop 内）

---

## 4. 数据模型变更

### 4.1 `NodeResult` 扩展

```python
@dataclass
class NodeResult:
    task_id: str
    status: NodeStatus
    summary: str
    child_run_id: str | None = None
    # 新增
    workspace_changes: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    error_detail: str | None = None
```

`summary` / `workspace_changes` / `artifact_ids` / `key_decisions` 均来自 `report_result` 的结构化 payload（REQ-0c）。兜底场景下 `summary` 来自 `_extract_run_final_text`，其余为空列表。

### 4.2 `DagExecContext` 扩展

```python
@dataclass
class DagExecContext:
    # 现有字段
    conversation_id: str
    trigger_message_id: str
    parent_run_id: str
    cancel_event: asyncio.Event
    dispatch_depth: int = 0
    dispatch_visibility: str = "visible"
    user_id: str | None = None
    workspace_path: str = ""
    # 新增
    dag_id: str = ""               # 唯一标识本次 dispatch_plan 调用
    all_task_ids: list[str] = field(default_factory=list)
```

### 4.3 `ToolContext` 扩展

```python
@dataclass
class ToolContext:
    # 现有字段
    conversation_id: str
    workspace_path: str
    agent_id: str
    run_id: str
    cancel_event: asyncio.Event
    hook_registry: Any = None
    last_post_hook_result: Any = None
    tool_names: list[str] | None = None
    dispatch_depth: int = 0
    dispatch_mode: str = "solo"
    user_id: str | None = None
    # 新增
    dag_id: str | None = None     # 当前 DAG ID（仅 subagent 模式有值）
    dag_task_id: str | None = None  # 当前节点在 DAG 中的 task_id
    parent_run_id: str | None = None  # 主 Agent 的 run_id（用于 ask_peer 向主 Agent 提问时暂存 mailbox）
```

### 4.4 `AgentSession`（新数据类）

```python
@dataclass
class AgentSession:
    task_id: str
    run_id: str
    agent_id: str
    conversation_id: str
    parent_run_id: str
    dispatch_depth: int
    status: Literal["running", "completed", "failed", "expired"]
    ask_count: int = 0
    created_at: int  # epoch ms
    system_prompt: str | None = None  # 缓存 build_adapter_input 产出的 system_prompt，供 mini-run / retry 复用
```

### 4.5 `AgentSessionRegistry`（新模块）

- 位置：`backend/app/services/agent_session_registry.py`
- 类型：进程内单例（类似 `pending_dispatch_plans`）
- 数据结构：`dict[task_id, AgentSession]` + `dict[dag_id, set[task_id]]` + `dict[parent_run_id, list[str]]`（主 Agent mailbox）
- 生命周期：DAG 开始时注册，DAG 结束后标记 `completed/failed`，N 秒后清理
- 线程安全：asyncio 单线程，无需锁
- **主 Agent mailbox**：独立于 `AgentSession`，存在 `AgentSessionRegistry` 上（`dict[parent_run_id, list[str]]`）。`ask_peer` 不指定 `peerTaskId` 时，将问题追加到 `mailbox[parent_run_id]` 列表。`dispatch_plan` 的 `execute_dag` 返回后，从 mailbox 取出所有问题，拼入 `dispatch_plan` 的 tool_result 返回值
- **system_prompt 缓存**：`AgentSession` 新增 `system_prompt: str | None` 字段，在子 Agent run 启动时（`build_adapter_input` 产出后）缓存——供 mini-run / retry 复用，确保 messages 前缀一致性，最大化 prefix cache 命中率

### 4.6 `ReportResultPayload`（新数据类）

```python
@dataclass
class ReportResultPayload:
    """Structured result from report_result tool (REQ-0a)."""
    summary: str
    key_decisions: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
```

- 存储位置：进程内 `_report_result_cache: dict[str, ReportResultPayload]`（key = `run_id`）
- 生命周期：`report_result` handler 写入 → `spawn_subagent_loop` 读取后清理（REQ-0c）
- 线程安全：asyncio 单线程，无需锁

### 4.7 `_report_result_cache`（进程内模块级缓存）

- 位置：`backend/app/tools/report_result.py` 模块级变量
- 类型：`dict[str, ReportResultPayload]`
- 写入：`report_result` handler（key = `ctx.run_id`）
- 读取+清理：`spawn_subagent_loop` 完成后（REQ-0c）
- 兜底清理：进程退出时自然回收；可选定时清理防止泄漏（如 run 异常终止未走正常提取路径）

### 4.8 无新增 DB 表

本需求不新增数据库表。`AgentSessionRegistry` 和 `_report_result_cache` 都是进程内字典，生命周期与进程一致。子 Agent 的对话历史已通过 `persist_event` 持久化到 `Message` 表（含 `hidden=True`），mini-run 直接从 Message 表重建。`agent_run_checkpoints` 表作为 system_prompt 回退来源保留（非主路径）。

---

## 5. 工具变更

### 5.0 新增工具：`report_result`

- **文件**：`backend/app/tools/report_result.py`
- **工具名**：`report_result`
- **参数**：见 REQ-0a
- **handler 行为**：
  1. 从 `args` 构造 `ReportResultPayload`（summary / key_decisions / files_changed / artifacts）
  2. 存入 `_report_result_cache[ctx.run_id]`
  3. 返回 `ok({"status": "reported"})`
- **终态属性**：`report_result` 在 `TERMINAL_TOOLS` 集合中（REQ-0b），调用后 ReAct loop 直接终止
- **注册位置**：`backend/app/tools/registry.py` 全局注册
- **注入条件**：`_run_subagent_loop` 中，始终注入（不限 depth）

### 5.0.1 修改 `_run_react_loop`：终态工具终止逻辑

- **文件**：`backend/app/services/agent_runner.py`
- **变更**：
  - 新增 `TERMINAL_TOOLS = frozenset({"report_result"})` 常量
  - 在 `_run_react_loop` 的 `len(tool_calls) > 0` 分支中，**在 `asyncio.gather` 执行工具、逐个追加 `res.tool_message` 到 `messages`、`term.record_tool_calls()` 之后**，检查是否包含终态工具
  - 如果包含：设置 `stop_reason = StopReason.COMPLETE`，发射 `POST_TURN` + `ON_STOP` hooks，`break`
  - 同 turn 内所有工具（包括非终态工具）的 `tool_result` 在检查前已追加到 `messages`——这是设计意图，使 checkpoint / retry 重建更完整
  - LLM 不会再看到这些 tool_result（loop 已终止），不存在信息遗漏

### 5.0.2 修改 `spawn_subagent_loop`：优先提取结构化结果

- **文件**：`backend/app/services/agent_loop.py`
- **变更**：
  - 子 Agent run 完成后，优先从 `_report_result_cache.get(child_run_id)` 提取 `ReportResultPayload`
  - 如果有：`LoopRunResult.text = payload.summary`，`artifact_ids = payload.artifacts`，新增 `workspace_changes = payload.files_changed`，新增 `key_decisions = payload.key_decisions`
  - 如果没有：回退到 `_extract_run_final_text`（现有路径），`workspace_changes` / `key_decisions` 为空列表
  - 取完后 `del _report_result_cache[child_run_id]`
  - `LoopRunResult` dataclass 新增 `workspace_changes: list[str]` 和 `key_decisions: list[str]` 字段

### 5.0.3 修改 `_run_subagent_loop`：注入 `report_result`

- **文件**：`backend/app/services/agent_loop.py`
- **变更**：
  - 在 `_run_subagent_loop` 中，始终将 `report_result` 加入 `tool_names`（不限 depth）
  - 在 `_SUBAGENT_SUFFIX` 中追加 `report_result` 使用指导（见 REQ-0d）

### 5.1 新增工具：`ask_peer`

- **文件**：`backend/app/tools/ask_peer.py`
- **工具名**：`ask_peer`
- **参数**：

```json
{
  "type": "object",
  "required": ["question"],
  "properties": {
    "peerTaskId": {
      "type": "string",
      "description": "同 DAG 内目标节点的 task_id。省略时向主 Agent 异步留言（主 Agent 在 DAG 结束后查看，不期望收到回复）。"
    },
    "question": {
      "type": "string",
      "description": "提问内容。需自包含——目标 Agent 只看到问题文本，看不到当前 Agent 的执行上下文。"
    }
  }
}
```

- **handler 行为**：
  1. 有 `peerTaskId`：从 `AgentSessionRegistry` 查找 `AgentSession`（获取 `run_id` / `agent_id` / `conversation_id`）→ 从 Message 表重建 R1 完整对话历史 → 复用 R1 的 system_prompt → 用 `session.agent_id` 创建 mini-run `RunArgs` → 等待完成 → 返回 answer
  2. 无 `peerTaskId`：将问题暂存到主 Agent mailbox → 返回 pending
  3. 目标 session 不存在或已过期：返回 `{ status: "unavailable" }`
  4. `ask_count` 达上限（3 次）：返回 `{ status: "limit_reached" }`
- **注册位置**：`backend/app/tools/registry.py` 全局注册
- **注入条件**：`_run_subagent_loop` 中，当 `dispatch_depth < MAX_DISPATCH_DEPTH` 时注入

### 5.2 修改工具：`dispatch_plan`

- **文件**：`backend/app/tools/dispatch_plan.py`
- **变更**：
  - `_handler` 中生成 `dag_id`（`new_tool_call_id()` 或 UUID）
  - 构建 `DagExecContext` 时传入 `dag_id` 和 `all_task_ids`
  - 返回结果增强：每个 task 附带 `workspace_changes` / `artifact_ids` / `error_detail`
  - 新增可选参数 `mode: "full" | "retry"`（默认 `"full"`）
  - 新增可选参数 `originalDagId: str`（`mode="retry"` 时使用，主 Agent 从上次 `dispatch_plan` 返回值中获取原 DAG ID 传入，用于按 `(originalDagId, task_id)` 查找原 session）

### 5.3 修改 `dag_executor._execute_node`

- **变更**：
  - 读取 `task.inputs`，从 `results` 中获取上游 `NodeResult`
  - 将上游结构化输出（summary / key_decisions / workspace_changes / artifact_ids）拼接到 `task_description`
  - `on_start` 回调中向 `AgentSessionRegistry` 注册 session（含 `run_id`）
  - 节点 `build_adapter_input` 完成后，将 `adapter_input.system_prompt` 缓存到 `AgentSession.system_prompt`
  - 节点完成后更新 session status
  - `NodeResult` 填充 `workspace_changes` / `artifact_ids` / `key_decisions` / `error_detail`（来自 `LoopRunResult` 的结构化字段，REQ-0c）

### 5.4 修改 `spawn_subagent_loop`

- **变更**：
  - 新增参数 `dag_id: str | None`、`dag_task_id: str | None`
  - 传入 `RunArgs`（新增对应字段）
  - `execute_simple_run` 创建 `ToolContext` 时注入 `dag_id` / `dag_task_id`
  - `build_adapter_input` 完成后，将 `adapter_input.system_prompt` 缓存到 `AgentSession.system_prompt`（供 mini-run / retry 复用）
  - 子 Agent run 完成后，优先从 `_report_result_cache` 提取结构化结果（见 §5.0.2）
  - `LoopRunResult` 新增 `workspace_changes` / `key_decisions` 字段

### 5.5 修改 `RunArgs`

- 新增字段：`dag_id: str | None = None`、`dag_task_id: str | None = None`、`parent_run_id: str | None = None`
- 新增字段：`override_messages: list[dict] | None = None`——mini-run 专用，在 `execute_run` 中 `build_adapter_input` 之后检查，非 `None` 时覆盖 `adapter_input.messages`
- 复用已有字段：`override_system_prompt: str | None = None`——已存在，mini-run 设此值覆盖 `build_adapter_input` 产出的 system_prompt（当前此字段仅在 solo/coordinated 模式使用，subagent 模式可复用）
- **注入时机**：在 `execute_run` 中 `build_adapter_input` 返回后、`resume_from_checkpoint` 逻辑之后、`_run_react_loop` 调用之前，检查 `args.override_messages is not None`，若是则设置 `adapter_input.messages = args.override_messages`；检查 `args.override_system_prompt is not None`，若是则设置 `adapter_input.system_prompt = args.override_system_prompt`

### 5.6 修改 `_run_subagent_loop`

- 在 `dispatch_depth < MAX_DISPATCH_DEPTH` 时，将 `ask_peer` 加入 `tool_names`
- 始终将 `report_result` 加入 `tool_names`（不限 depth）

---

## 6. 事件变更

### 6.1 新增事件（可选，用于 UI 可观测性）

| 事件 | 触发时机 | 字段 |
|------|----------|------|
| `PeerAskStart` | `ask_peer` 创建 mini-run 时 | `conversationId`, `parentRunId`, `peerTaskId`, `childRunId`, `question` |
| `PeerAskEnd` | mini-run 完成时 | `conversationId`, `parentRunId`, `peerTaskId`, `childRunId`, `status`, `answer` |

> 这两个事件是可选的，用于前端展示子 Agent 间的交互过程。如果前端不支持，可仅作为 trace span 记录。

### 6.2 现有事件不受影响

`DispatchStartEvent` / `DispatchEndEvent` / `DispatchPlanEvent` 的 schema 不变。mini-run 产生的事件走标准 `RunStart` / `RunEnd` 路径，但 `hidden=True`。

---

## 7. 不改动的部分

| 组件 | 原因 |
|------|------|
| `_run_react_loop` 的 while-loop 结构 | D-1 不需要暂停/恢复 generator。但新增终态工具终止逻辑（REQ-0b），改动范围限于 `len(tool_calls) > 0` 分支 |
| `_run_react_loop` 的 `len(tool_calls) == 0` 终止路径 | 自然终止路径完全保留，不受终态工具影响 |
| `execute_simple_run` 的核心流程 | mini-run 仍走此路径（含 `build_adapter_input`），不加 skip flag。`build_adapter_input` 产出的 `system_prompt` 和 `messages` 在进入 `_run_react_loop` 前被覆盖 |
| `persist_event` 的持久化逻辑 | 子 Agent 消息已持久化到 Message 表（含 hidden=True），mini-run 直接查询重建 |
| `build_history_for` 的 parts→messages 转换逻辑 | 复用其内部转换逻辑，但跳过 hidden 过滤（mini-run 需要含 hidden 消息） |
| `worktree_service` 的 create/merge/cleanup | worktree 生命周期不变 |
| `MAX_DISPATCH_DEPTH = 3` | 不改深度上限，`ask_peer` 创建的 mini-run 受同一限制 |
| `topological_waves` / `validate_dag` | DAG 验证和波调度逻辑不变 |
| CLI Agent（Claude Code / Codex） | CLI agent 不参与 baseline 合并，不注入 `ask_peer` / `report_result` |
| solo / coordinated 模式 | 不注入 `report_result` / `ask_peer`（solo/coordinated 直接面向用户） |

---

## 8. 风险与折中

### 8.1 `ask_peer` 向主 Agent 留言是异步的（已接受的设计折中）

主 Agent 在 `await execute_dag()` 期间无法响应。`ask_peer`（无 `peerTaskId`）返回 pending，主 Agent 在 DAG 执行结束后才看到留言。子 Agent 的 run 在节点完成后即结束，**永远收不到回复**。

**设计定位**：REQ-6 不是同步 Q&A，而是异步留言通道。价值在于子 Agent 可将阻塞问题 / 需主 Agent 决策的事项结构化传递给主 Agent，主 Agent 在 DAG 结束后集中处理（retry / 调整 / 接受）。此设计折中已接受，不视为风险。

**替代路径**：如果子 Agent 需要同步回复才能继续工作，应走 REQ-5 的 mini-run 路径（向已完成的上游节点提问）。

### 8.2 system_prompt 复用可能回退

mini-run 仍走 `build_adapter_input` 标准路径（`override_prompt` 非空时自动跳过 history / PromptAssembler），但产出的 `system_prompt` 只含 `agent.system_prompt` + workspace context，缺少 R1 执行时的 RAG / dynamic_prefix。优先从 `AgentSession` 缓存或 checkpoint 取 R1 的完整 system_prompt 来覆盖。如果两者都不可用（AgentSession 未缓存 system_prompt 且 checkpoint 不存在），则不覆盖——使用 `build_adapter_input` 刚产出的轻量 system_prompt。此时 system_prompt 与 R1 不一致（缺少 RAG / dynamic_prefix），导致 prefix cache miss。

**缓解**：
- `AgentSession` 在子 Agent run 启动时缓存 `build_adapter_input` 产出的 system_prompt（零额外开销——已经在内存中）
- 回退路径仍功能正常，只是 KV cache 命中率降低

### 8.3 mini-run 的 KV Cache 复用

mini-run 从 Message 表重建完整 messages + 复用目标 Agent 的 system_prompt，发给 LLM 的第一条 API 请求的 messages 前缀与目标 Agent 最后一条 API 请求完全一致——DeepSeek prefix cache 命中率最大化。

**限制**：
- prefix cache 在 LLM 服务端有 TTL（通常 5-10 分钟），如果 t1 完成到 t2 调 ask_peer 之间间隔太久（如 DAG 有其他耗时节点），缓存可能已过期
- mini-run 新增的 user message（问题）及之后的 tool 调用不在 cache 范围内——这是 API 无状态的固有限制
- 此限制不可控（取决于 LLM provider 的缓存策略），但在 DAG 同波执行场景下间隔通常较短

### 8.4 `ask_peer` 可能被 LLM 过度使用

LLM 可能倾向于频繁调用 `ask_peer` 而非自己解决问题，导致 DAG 执行时间过长。

**缓解**：
- 同一 `peerTaskId` 的 `ask_count` 上限 3 次
- `ask_peer` 工具描述明确指导："仅当上游产出缺少关键信息且无法自行推断时使用"
- 子 Agent system prompt（`_SUBAGENT_SUFFIX`）追加 `ask_peer` 使用指导

### 8.5 AgentSessionRegistry 是进程内的，不支持多进程

如果后端多进程部署，`AgentSessionRegistry` 无法跨进程共享。

**当前可接受**：AChat 是本地运行的单进程应用（CLAUDE.md §1）。如果未来需要多进程，可降级为 DB 表（但当前不需要）。

### 8.6 Agent 可能不调用 `report_result` 就结束

LLM 可能忽略 `_SUBAGENT_SUFFIX` 中的指导，直接以 `end_turn`（0 tool calls）结束，不调 `report_result`。

**兜底**：REQ-0c 设计了完整的回退路径——`spawn_subagent_loop` 检查 `_report_result_cache` 为空时，回退到 `_extract_run_final_text`（取最后一条消息文本）作为 `summary`，`workspace_changes` / `artifact_ids` / `key_decisions` 为空列表。下游注入和 `dispatch_plan` 返回仍能工作，只是信息完整性降低。

**缓解**：
- `_SUBAGENT_SUFFIX` 中的指导措辞强烈（"**必须调用**"）
- 可在 system prompt 中追加更明确的指导
- 长期可通过模型 fine-tuning 或 few-shot 示例提高调用率

### 8.7 终态工具与同 turn 多工具调用的交互

如果 model 在同一 turn 内同时调用 `report_result` 和其他工具（如 `fs_write`），所有工具的结果**都会正常执行和持久化**，且所有 `tool_result` 消息**都会追加到 `messages`**（因为追加发生在终态检查之前）。终态工具检查后 loop 终止，LLM 不会再进入下一轮 model call。

**影响**：
- 工具的执行结果不受影响（所有工具 handler 正常执行，事件正常 yield）
- `messages` 列表包含同 turn 内所有 tool_result——这使得 checkpoint / retry 重建时上下文更完整
- LLM 不会再看到这些 tool_result（loop 已终止）——这是设计意图，不存在信息遗漏

**SSE 可见性**：subagent 模式下消息 `hidden=True`，`_VISIBLE_EVENT_TYPES` 包含 `tool.result`，但 `consume_stream` 中 `hidden=True` 时不会发布 visible event types 到 SSE 总线。因此 `report_result` 的 tool result 不会泄露到前端。

### 8.8 `_report_result_cache` 可能内存泄漏

如果 run 异常终止（如进程崩溃、asyncio task 被 cancel），`_report_result_cache` 中的 entry 可能不会被清理。

**缓解**：
- 正常路径：`spawn_subagent_loop` 取完后清理（REQ-0c）
- 异常路径：进程退出时自然回收
- 可选：定期清理超过 N 分钟的 entry（如 30 分钟）

---

## 9. 验收标准

### 9.1 `report_result` 终态工具

- [ ] `report_result` 工具已注册在 `tool_registry`
- [ ] subagent 模式注入 `report_result`（不限 depth）
- [ ] solo / coordinated 模式不注入 `report_result`
- [ ] 子 Agent 调用 `report_result` 后，ReAct loop 直接终止（不再进入下一轮 model call）
- [ ] 终态工具终止时 `stop_reason = COMPLETE`
- [ ] `_report_result_cache` 正确存储和清理（`spawn_subagent_loop` 取完后清理）
- [ ] 子 Agent 未调 `report_result` 时，回退到 `_extract_run_final_text` 兑底路径正常工作
- [ ] `LoopRunResult` 新增 `workspace_changes` / `key_decisions` 字段正确填充
- [ ] `_SUBAGENT_SUFFIX` 包含 `report_result` 使用指导

### 9.2 DAG Edge 数据传递

- [ ] 当 `t2` 依赖 `t1` 且 `t1` 成功完成时，`t2` 的子 Agent 在 task_description 中能看到 `t1` 的结构化输出（summary / key_decisions / files_changed / artifacts）
- [ ] 多上游依赖时，输出按 `dependsOn` 顺序拼接
- [ ] `DispatchPlanItem.inputs` 字段被正确读取和注入
- [ ] workspace_changes 在 task_description 中以结构化格式呈现
- [ ] 兑底场景（上游未调 `report_result`）下，自然语言 summary 正确注入，其他字段为空

### 9.3 `ask_peer` 工具

- [ ] 子 Agent 可通过 `ask_peer` 向同 DAG 内已完成节点提问
- [ ] `ask_peer` 创建的 mini-run 从 Message 表重建完整对话历史（含 hidden 消息）
- [ ] mini-run 走 `build_adapter_input` 标准路径（不跳过），产出后 `system_prompt` / `messages` 被覆盖
- [ ] mini-run 的 `dispatch_depth` 正确递增
- [ ] mini-run 的消息 `hidden=True`
- [ ] 同一 `peerTaskId` 的 `ask_count` 达 3 次时返回 `limit_reached`
- [ ] `ask_peer` 不指定 `peerTaskId` 时，反馈暂存到主 Agent mailbox（异步留言，不期望回复）
- [ ] 目标 session 不存在时返回 `unavailable`

### 9.4 局部重试

- [ ] `dispatch_plan` 返回结果包含每个 task 的 `workspace_changes` / `artifact_ids` / `key_decisions` / `error_detail`
- [ ] `dispatch_plan(mode="retry")` 可引用原 DAG 中已成功完成的节点 ID
- [ ] retry 模式下，系统从 Message 表重建上次对话历史（含 hidden 消息）+ 复用 system_prompt
- [ ] session 在 DAG 结束后保留 N 秒供 retry 使用
- [ ] session 过期后 retry 仍可工作（从 `AgentRun` 表反查 `run_id`，system_prompt 走 `build_adapter_input` 重新构造）
- [ ] retry 时如重建 messages 的 `ratio ≥ COMPACT_MASK_RATIO (0.75)`，调用 `should_compact` + `run_compact_pipeline_unified` 压缩后再追加 retry 指令

### 9.5 不破坏现有功能

- [ ] `task_dispatch` 工具行为不变
- [ ] `dispatch_plan` 默认 `mode="full"` 行为与当前一致
- [ ] 现有 DAG 测试（`test_dag_executor.py` / `test_dispatch_plan_tool.py`）全部通过
- [ ] CLI Agent 不受影响（不注入 `ask_peer` / `report_result`）
- [ ] solo 模式不注入 `ask_peer` / `report_result`
- [ ] coordinated 模式不注入 `ask_peer` / `report_result`
- [ ] `MAX_DISPATCH_DEPTH = 3` 限制不被绕过
- [ ] `_run_react_loop` 的 `len(tool_calls) == 0` 终止路径不受影响
- [ ] `force_final` 路径不受终态工具逻辑影响

---

## 10. 文件影响矩阵

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `backend/app/tools/report_result.py` | **新增** | `report_result` 终态工具定义 + handler + `ReportResultPayload` + `_report_result_cache` |
| `backend/app/tools/ask_peer.py` | **新增** | `ask_peer` 工具定义 + handler |
| `backend/app/services/agent_session_registry.py` | **新增** | AgentSessionRegistry 进程内单例 |
| `backend/app/services/dag_executor.py` | 修改 | `_execute_node` 注入上游结构化输出 + 注册 session + `NodeResult` 扩展（含 `key_decisions`） |
| `backend/app/tools/dispatch_plan.py` | 修改 | 生成 dag_id + 增强返回结果（含 `key_decisions`）+ `mode` 参数 |
| `backend/app/services/agent_loop.py` | 修改 | `spawn_subagent_loop` 新增参数 + 结构化结果提取 + `_run_subagent_loop` 注入 `ask_peer` + `report_result` + `_SUBAGENT_SUFFIX` 追加指导 + `LoopRunResult` 新增字段 |
| `backend/app/services/agent_runner.py` | 修改 | `RunArgs` 新增字段（`dag_id` / `dag_task_id` / `parent_run_id` / `override_messages`）+ `ToolContext` 构建时注入 dag 上下文 + `_run_react_loop` 终态工具终止逻辑 + `TERMINAL_TOOLS` 常量 + `execute_run` 中新增 `override_messages` / `override_system_prompt` 覆盖注入点 |
| `backend/app/services/conversation_context.py` | 修改 | 抽取 parts→messages 转换逻辑为可复用函数，支持 `include_hidden=True` 参数（mini-run 需要含 hidden 消息） |
| `backend/app/tools/base.py` | 修改 | `ToolContext` 新增 `dag_id` / `dag_task_id` 字段 |
| `backend/app/tools/registry.py` | 修改 | 注册 `ask_peer` + `report_result` 工具 |
| `backend/app/schemas/dispatch.py` | 修改 | `DispatchPlanItem` 无 schema 变更（`inputs` 已存在），但文档补充 |
| `backend/app/schemas/events.py` | 可选修改 | 新增 `PeerAskStart` / `PeerAskEnd` 事件（可选） |
| `src/shared/` | 可选修改 | 前端类型同步（如新增事件） |
| `specs/19-unified-agent-loop.md` | 修改 | 补充 `ask_peer` + `report_result` 工具 + DAG 数据传递章节 + 终态工具概念 |

---

## 11. 依赖关系

```
REQ-0 (report_result + 终态终止) ──────────────┐
  ├─ REQ-0a (report_result 工具)               │
  ├─ REQ-0b (ReAct loop 终态终止)             │
  ├─ REQ-0c (结构化结果提取)                  ├──→ REQ-10 (结构化返回) ──→ REQ-11 (增量 retry)
  └─ REQ-0d (prompt 指导)                      │
                                               │
REQ-1 (上游输出注入)     ──────┐              │
REQ-2 (workspace 变更)   ───┐  │              │
REQ-3 (inputs 字段)      ──┤  │              │
                              ├──→ 依赖 REQ-0  │
REQ-8 (SessionRegistry)  ──┤  │              │
REQ-9 (DAG 上下文传递)   ──┤  │              │
                              │               │
REQ-4 (ask_peer 工具)    ────┤               │
REQ-5 (mini-run 创建)    ────┤──→ REQ-7 (防环) │
REQ-6 (主 Agent mailbox) ────┘               │
```

- **REQ-0 是最前置**：`report_result` + 终态终止逻辑是所有上游输出传递的基础
- REQ-1/2/3 依赖 REQ-0（上游输出数据来源为 `report_result` 结构化 payload）
- REQ-8/9 是基础设施（SessionRegistry + 上下文传递），`ask_peer` 依赖它们
- REQ-4/5/6 是 `ask_peer` 工具的三条路径
- REQ-10/11 是主 Agent 侧增强，依赖前面所有需求

建议实现顺序：REQ-0 → REQ-1 → REQ-2 → REQ-3 → REQ-8 → REQ-9 → REQ-4 → REQ-5 → REQ-6 → REQ-7 → REQ-10 → REQ-11
