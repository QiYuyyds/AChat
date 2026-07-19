## Context

AChat 的 SDK ReAct loop（`_run_react_loop`，agent_runner.py:870）是一个 while 循环：调模型 → 收 tool_calls → 执行 tools → 把结果回灌进 messages[] → 再调模型。直到模型不再调工具（model-done）或终止状态机接管。

每轮的 messages[] 在内存里累积。一个 8 turn × 平均 7 工具的"分析项目"任务会产生 56+ 条 message、~100k tokens。当 ratio（total_tokens / model_limit）达到 0.90，`_mid_run_compact`（agent_runner.py:836）触发结构性压缩。

### 现状的五个核心缺陷

1. **`recent_keep = 6` 按 message 数切，不是按 turn 切**：一个 7 工具 turn = 8 条 message，保留 6 条等于保留 0.75 个 turn。模型在压缩后看不到"上一轮我调了什么、得到了什么"。
2. **无差别销毁式剪裁**：所有 tool_result 超过 2000 tokens 一律替换成 `"[tool_result 已裁剪（mid-run compact）]"`。fs_list 的项目结构、fs_read 的文件内容、bash 的输出被同等对待。
3. **`success` 判定过松**（agent_runner.py:1035）：`success = post_tokens < pre_tokens or len(messages) < pre_compact_count`。fold 路径永远让 len 变小，所以永远 success，`COMPACT_FAILURE_THRESHOLD=3` 永远走不到，`compact_disabled` 永远不触发。
4. **token 估算系统性偏高**（agent_runner.py:1010）：`estimate_tokens(json.dumps(messages))` 把 `role`、`tool_call_id`、`type`、`function` 等 JSON 元数据也算 token，实际偏高 15-25%，让 ratio 提前到 0.90。
5. **死胡同 marker**：`"[tool_result 已裁剪]"` 告诉模型"详见 message_id=xxx"但没有 `read_message` 工具能取回。信息彻底丢失。

### 与其他压缩层的关系

AChat 有四层压缩系统：Tier 0（本设计）+ Tier 1（`build_history_for` 跨 run 剪裁，conversation_context.py）+ Tier 2/3（`compact_conversation` LLM-backed 全量压缩，context_compaction_service.py）+ Session Memory（增量摘要，session_memory.py）。本设计仅改 Tier 0，不动其他三层。但 Tier 0 的改进会让 Tier 4/5（soft wrap-up / forced final）触发频率下降——前 3 阶段把 ratio 压下去。

## Goals / Non-Goals

**Goals:**

- 把单点 0.90 触发的 `_mid_run_compact` 替换为五阶段 pipeline（0.70 / 0.80 / 0.88 / 0.93 / 0.95），早期轻剪保留语义，后期重度折叠。
- 按 tool name + mode 差异化保留语义骨架（fs_list 保 name/relativePath、fs_read full 转 outline、bash 保末 N 行、code_explore 完整保留）。
- 按 turn 边界剪裁，保留末 2 个完整 turn，不切断 `tool_use ↔ tool_result` 配对。
- 修正 token 估算：只算 `content + tool_calls.function.name/arguments + reasoning_content`，不算 JSON 元数据。
- 成功判定改严：要求 token 实际下降 ≥15%，让 `compact_disabled` 断路器能真正触发。
- Marker 携带 `tools_used` / `summary` / `recover_hint` 元信息，把死胡同换成可恢复 marker。

**Non-Goals:**

- 不动 Tier 1（跨 run 剪裁）、Tier 2/3（LLM 全量压缩）、Session Memory。这些是后续独立 change。
- 不引入 LLM-backed 文件摘要（阶段 1 的 outline 用现成 `extract_outline` 纯正则）。
- 不引入真实 tokenizer（tiktoken 等）。content-only 估算 + 阈值补偿已够用。
- 不改 `SOFT_WRAPUP_INSTRUCTION` / `FORCED_FINAL_INSTRUCTION` / `STOP_REASON_LABELS`。阶段 4/5 行为完全保留。
- 不改 DB schema、API、SSE 事件契约。Tier 0 仍只动 in-memory messages[]。
- 不改 CLI adapter（Claude Code / Codex）。本变更只影响 SDK adapter 走的 `_run_react_loop` 路径。
- 不改 sub-agent dispatch 路径。`spawn_subagent_loop` 也走 `_run_react_loop`，但本次不专门为子 agent 调参。

## Decisions

### D1. 五阶段 pipeline 而非单点 compact

- **选择**：把 `_mid_run_compact` 替换为 `_run_compact_pipeline(messages, stage)`，按 stage 1/2/3 分级触发。stage 4/5（soft_inject / force_final）保留不动。
- **理由**：
  - 单点 0.90 触发太晚——到 0.90 时上下文已 90% 满，留给阶段 4/5 的余量不足，模型容易在失忆状态下硬撑。
  - 分级触发让早期（0.70）做轻剪（保留语义骨架），中期（0.80）做中剪（再压骨架），后期（0.88）做重剪（fold）。每阶段 token 预期下降 40-60%，到 0.93 时模型仍有完整 turn 7-8 + 摘要印象。
  - 阶段 1/2/3 不调 LLM（纯正则 + 结构剪裁），延迟零增加。
- **替代方案**：保持单点但提高阈值到 0.85 + 改 success 判定。被否决——单点意味着"一次性销毁"，不论阈值多准，一旦触发就丢失大量语义。

### D2. 按 tool 类型差异化保留（策略表）

- **选择**：定义 `ToolResultSummarizer` 策略表，按 tool name + mode 决定保留字段。

  | tool name + mode | 阶段 1（轻） | 阶段 2（中） | 阶段 3（重） |
  |---|---|---|---|
  | fs_list(depth=1) | 完整保留 | 保留 entries.name | 仅"目录 X 含 N 文件" |
  | fs_list(depth>1) | 保留 name+relativePath | 仅 directory tree | 仅根目录结构 |
  | fs_read(mode=full) | 转 outline（调 extract_outline） | 首 3 行 + outline 头部 | 仅"文件 X 有 N 行" |
  | fs_read(mode=outline) | 完整保留 | 完整保留 | outline 前 5 条 |
  | fs_read(mode=head) | 完整保留 | 完整保留 | 完整保留 |
  | fs_grep | 前 10 matches | 前 5 matches | 仅"找到 N 处匹配" |
  | bash | 末 20 行 | 末 5 行 | 仅 exit_code + 末 1 行 |
  | code_explore | 完整保留 | 完整保留 | 完整保留（密度高） |
  | 其他 | 前 1k chars | marker | 折叠 |

- **理由**：
  - `fs_list(depth>1)` 的结构信息（name + relativePath）是后续 fs_read 路径推理的依据——丢了就幻觉。size/depth/isDirectory 字段对推理无用，可丢。
  - `fs_read(mode=full)` 调 `extract_outline`（fs_service.py:487，已存在）转骨架，token 消耗约为 full 的 1/10。零额外依赖。
  - `code_explore` 本身已是高层摘要（max 30k chars ≈ 7.5k tokens），剪了等于白调，永远完整保留。
  - `fs_read(mode=outline/head)` 本来就短（< 2000 tokens），永远完整保留。
- **替代方案**：所有 tool_result 一视同仁，按统一阈值剪。被否决——这正是现状的缺陷。

### D3. 按 turn 边界剪裁（替换 recent_keep=6）

- **选择**：新增 `TurnBoundaryFinder`，找出每个 turn 的起止 index（1 条 assistant 含 tool_calls + 紧跟的 N 条 tool messages）。`KEEP_RECENT_TURNS = 2` 保留末 2 个完整 turn。
- **理由**：
  - ReAct loop 的语义单元是 turn，不是 message。切断 turn 会留下孤立的 tool_use（无对应 tool_result）或孤立的 tool_result（无对应 tool_use），让模型混乱。
  - 保留末 2 个完整 turn 让模型至少能看到"上一轮我调了什么、得到了什么"，避免完全失忆。
  - 配合阶段 1/2 的语义摘要，2 个完整 turn + 摘要足够推理。
- **fallback**：如果 messages[] 里没有完整 turn 边界（如所有 assistant message 都没 tool_calls），fallback 到老的 message-count 策略（`recent_keep=6`），加日志警告。
- **替代方案**：`KEEP_RECENT_TURNS = 3`。被否决——3 个 turn 可能 30-40k tokens，压缩效果差。2 个 + 摘要足够。

### D4. Token 估算修正（content-only）

- **选择**：新增 `estimate_messages_tokens(messages)`，只算 `content` + `tool_calls.function.name/arguments` + `reasoning_content`，不算 `role/tool_call_id/type` 等 JSON 元数据。每条 message +4 tokens overhead。
- **理由**：
  - `json.dumps` 包含 `"role": "assistant"`、`"tool_call_id": "call_xxx"`、`"type": "function"`、`"reasoning_content": "..."` 等元数据。4 字符 ≈ 1 token 的粗算让这些字段也算 token，实际偏高 15-25%。
  - content-only 估算更接近真实 LLM token 数。配合把 `COMPACT_RATIO` 从 0.90 调到 0.85，给后置触发留余量。
- **替代方案**：引入 tiktoken 真实 tokenizer。被否决——违反 CLAUDE.md §6.2"新增依赖需先问"。且不同 provider 用不同 tokenizer（DeepSeek vs OpenAI），统一难。content-only + 阈值补偿已够用。

### D5. 成功判定改严（防假成功 + 防空循环）

- **选择**：新增 `CompactSuccessJudge`，`success = post_tokens < pre_tokens * 0.85`（至少降 15%）。`len` 变化不再算成功。
- **理由**：
  - 现状 `len(messages) < pre_compact_count` 在 fold 路径下永远成立（fold 把多条合并成 1 条 marker），所以永远 success，`COMPACT_FAILURE_THRESHOLD=3` 永远走不到。
  - 改严后，如果阶段 1/2/3 剪了但 token 没降（比如纯文本消息无 tool_result 可剪），累计 3 次失败 → `compact_disabled = True` → 跳过 compact 走 soft_inject。让模型在"压不动"时及时收尾，而不是在失忆状态下硬撑。
- **防空循环**：失败后 `continue` 重新 `decide_pre_model`，如果 ratio 仍 ≥ 0.88 会再次触发 stage 3。但 stage 3 已无完整 turn 可 fold（都被剪过了），会再次失败。累计 3 次后 compact_disabled 阻断 compact 路径，直接走 soft_inject。

### D6. Marker 携带元信息（结构化纯文字）

- **选择**：fold marker 和 tool_result 替换 marker 用结构化纯文字而非 JSON。格式：
  ```
  [compacted stage=1 tool=fs_list path=src depth=3]
  [summary: src/ 下 5 文件、3 子目录]
  [recover: fs_list(path='src', depth=3) 重新获取结构]
  ```
  fold marker：
  ```
  [folded stage=3 turns=3 tools: fs_list×2 fs_read×5 bash×1]
  [summary: 本段主要探索了 src/ 与 backend/ 目录结构]
  ```
- **理由**：
  - JSON marker 占 token 多（key 名也占字符），且某些模型对 JSON-in-content 解析不稳定。
  - 纯文字 marker 省 token，结构化字段（`stage=` / `tool=` / `summary:`）让模型可解析。
  - `recover` 字段把死胡同 marker 换成可恢复 marker——模型知道"如何重新获取"。
- **上限**：单个 marker ≤ 500 字符。`tools_used` 只列 top 5 工具 + count。`summary` 限 200 字符。
- **替代方案**：JSON marker。被否决——token 开销大，解析不稳定。

### D7. 阈值参数集中管理

- **选择**：所有阈值集中在新模块 `compact_pipeline.py` 顶部，方便调参：

  ```python
  STAGE1_SUMMARIZE_RATIO = 0.70
  STAGE2_PRUNE_RATIO = 0.80
  STAGE3_FOLD_RATIO = 0.88
  # COMPACT_RATIO 改为 0.85（react_loop_termination.py）
  KEEP_RECENT_TURNS = 2
  FOLD_TURN_THRESHOLD = 4
  EFFECTIVE_COMPACT_RATIO = 0.85  # success 判定
  COMPACT_FAILURE_THRESHOLD = 3  # 不变
  MAX_MARKER_CHARS = 500
  MAX_SUMMARY_CHARS = 200
  ```

- **理由**：调参时改一处即可，不用在 agent_runner.py / react_loop_termination.py / compact_pipeline.py 多处同步。

## Risks / Trade-offs

- **[阶段 1 的 outline 可能漏关键定义]** → 缓解：复用 `extract_outline` 的 fallback 机制（提取不到就返回空 outline + note 建议改 full）。阶段 1 只剪"末 2 turn 之前"的，最近内容仍完整。marker 的 `recover` 字段提示模型如何重新获取。

- **[阶段 3 fold 后 marker 可能太大]** → 缓解：`tools_used` 只列 top 5 工具 + count，`summary` 限 200 字符，单 marker ≤ 500 字符硬上限。

- **[Token 估算改严后 ratio 后置触发]** → 缓解：COMPACT_RATIO 从 0.90 调到 0.85。但 STAGE1_RATIO 仍用 0.70（早期轻剪不依赖精确估算）。如果生产中发现后置过头，可调回 0.88。

- **[TurnBoundaryFinder 找不到完整 turn]** → 缓解：fallback 到老的 message-count 策略（`recent_keep=6`），加日志警告"no turn boundary found, falling back to count-based"。极端情况（所有 assistant message 都没 tool_calls）走 fallback 路径。

- **[与 Session Memory 协同]** → 不在本次范围。Tier 0 不查 DB，保持纯内存操作。阶段 3 fold 的 summary 字段用模型上次输出的 text head，不依赖 Session Memory。后续可优化：fold 时查 Session Memory 覆盖这段则复用其 summary，但留给独立 change。

- **[stage 1/2/3 触发顺序的副作用]** → 阶段 1 触发后 `continue` 重新 `decide_pre_model`。如果阶段 1 后 ratio 仍 ≥ 0.70，会再次触发阶段 1（同一个函数）。但阶段 1 只剪"末 2 turn 之前"的 tool_result，第二次进入时已无可剪——`judge_compact_success` 返回 False，累计失败。3 次失败后 compact_disabled 阻断，走 soft_inject。这是预期行为（压不动就收尾），但需要测试覆盖避免死循环。

## Migration Plan

无 DB 迁移、无 API 变更、无事件契约变更。本变更纯后端 Python 实现，分以下阶段部署：

1. **新增模块**：`compact_pipeline.py` + `compact_markers.py`，先写好但不接入。
2. **修改 `react_loop_termination.py`**：扩展 `DecisionAction` 枚举（`"summarize"` / `"prune"` / `"fold"`），新增 `STAGE1/2/3_RATIO` 阈值，`decide_pre_model` 按新阈值返回新 action。`COMPACT_RATIO` 调到 0.85。
3. **修改 `agent_runner.py`**：`_mid_run_compact` 替换为 `_run_compact_pipeline(messages, stage)`；`total_tokens` 改用 `estimate_messages_tokens`；compact 分支按 action 分发到 stage 1/2/3。
4. **测试覆盖**：阶段 1/2/3 单元测试、TurnBoundaryFinder 边界测试、estimate_messages_tokens 偏差测试、judge_compact_success 三态测试、端到端 56-message 场景测试。
5. **回滚策略**：如生产环境发现回归（如 stage 1 过早触发压缩影响对话质量），可通过 settings 加 `compact_pipeline_enabled: bool = True` 开关一键回退到旧 `_mid_run_compact`。开关默认 True（启用新 pipeline），False 走旧路径。

## Open Questions

1. **`KEEP_RECENT_TURNS = 2` 是否足够？** 配合阶段 1/2 的语义摘要，2 个完整 turn + 摘要足够推理。但极端情况（一个 turn 调 15 工具、产生 60k tokens）下 2 个 turn 就 120k，配合 1M 窗口仍可接受。如果生产中发现不足，可调到 3。

2. **阶段 1 是否真的不调 LLM？** 阶段 1 的 fs_read full → outline 调 `extract_outline`（纯正则），不调 LLM。但 `extract_outline` 的正则覆盖不全（Python 装饰器、Rust 宏、JSX 内联组件）。生产中如果发现 outline 漏关键定义导致模型重新调 fs_read full，可考虑加 LLM-backed outline 作为可选优化——但不在本次范围。

3. **`COMPACT_RATIO = 0.85` 是否过激？** 现状 0.90 + json.dumps 估算偏高，等价于真实 ~0.70-0.75 触发。改 content-only 估算 + 0.85，等价于真实 ~0.80 触发。比现状略晚。如果发现太晚，可调回 0.88。

4. **sub-agent dispatch 是否需要独立调参？** `spawn_subagent_loop` 也走 `_run_react_loop`，但子 agent 任务通常更短，可能不需要 5 阶段。本次不专门为子 agent 调参，统一用主 agent 的阈值。生产中如发现子 agent 过早压缩，可加 `subagent_compact_ratio_override` 配置。
