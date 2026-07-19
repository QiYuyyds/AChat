## Why

SDK ReAct loop（`_run_react_loop`）内的 in-memory 压缩（Tier 0）目前是"单点 0.90 阈值 + 无差别销毁式剪裁"。一个 7 工具的 turn = 8 条 message，但 `recent_keep=6` 保留不到一个完整 turn；`_mid_run_compact` 把 fs_list 结构结果和 fs_read 文件内容都替换成 `"[tool_result 已裁剪]"` 死胡同 marker；`success` 判定过松（`len` 变了就算成功），导致 `compact_disabled` 断路器永远不触发；`estimate_tokens(json.dumps(messages))` 把 JSON 元数据也算 token，让 ratio 系统性偏高 15-25%。结果是模型在压缩后"失忆"，但仍未被 soft/forced final 接管，继续在失忆状态下硬撑——这是 Run 内压缩的核心病根。

## What Changes

- **五阶段压缩 Pipeline** 替换单点 `_mid_run_compact`：ratio ≥ 0.70 触发阶段 1（语义摘要），≥ 0.80 触发阶段 2（中度裁剪），≥ 0.88 触发阶段 3（按 turn 边界 fold）。阶段 4（soft wrap-up @ 0.93）和阶段 5（forced final @ 0.95）保留不动。
- **按 tool 类型差异化保留语义骨架**：fs_list 保留 `entries.name + relativePath`；fs_read full 调用现有 `extract_outline` 转骨架；bash 保留末 N 行；code_explore 永远完整保留。不再无差别替换成 marker。
- **按 turn 边界剪裁** 替换 `recent_keep=6`：保留末 `KEEP_RECENT_TURNS=2` 个完整 turn（1 条 assistant + 紧跟的 N 条 tool messages），不切断 `tool_use ↔ tool_result` 配对。
- **Token 估算修正**：只算 `content + tool_calls.function.name/arguments + reasoning_content`，不算 `role/tool_call_id/type` 等 JSON 元数据。COMPACT_RATIO 从 0.90 调到 0.85 以补偿估算后置。
- **成功判定改严**：`success` 要求 token 实际下降 ≥15%（`post_tokens < pre_tokens * 0.85`），不再以 `len` 变化为标准。3 次失败累计后真正触发 `compact_disabled`，跳过 compact 走 soft_inject。
- **Marker 携带元信息**：fold marker 包含 `tools_used`、`summary`、`recover_hint` 字段，把"死胡同 marker"换成"可恢复 marker"。
- **新增模块**：`compact_pipeline.py`（五阶段主干 + ToolResultSummarizer + TurnBoundaryFinder）、`compact_markers.py`（CompactMarkerBuilder + CompactSuccessJudge）。

## Capabilities

### New Capabilities

- `run-internal-compaction`: ReAct loop 内 in-memory messages 的压缩策略，独立于跨 run 的 conversation-context。覆盖触发时机、剪裁策略、保留范围、成功判定、marker 格式。

### Modified Capabilities

（无）——本变更全部落在新增 capability 上，不修改 `conversation-context`（其 spec 13 明确限定"within-run 工具调用链仍由 adapter 内部维护，本节只处理已落库的历史消息"）、`tools`（不涉及工具 schema 变更）、`orchestrator`（不涉及调度流程变更）的现有 requirements。

## Impact

- **后端**：
  - `backend/app/services/agent_runner.py:836-866` — 替换 `_mid_run_compact` 为 `_run_compact_pipeline(messages, stage)`；删除 `recent_keep` / `fold_threshold` / `keep_recent` 常量。
  - `backend/app/services/agent_runner.py:1010` — `total_tokens` 改用 `estimate_messages_tokens`。
  - `backend/app/services/agent_runner.py:1031-1046` — compact 分支扩展为按 stage 1/2/3 分别调用，引入 `judge_compact_success`。
  - `backend/app/services/react_loop_termination.py:301-458` — `decide_pre_model` 增加 `STAGE1_RATIO=0.70` / `STAGE2_RATIO=0.80` / `STAGE3_RATIO=0.88` 阈值，`DecisionAction` 增加 `"summarize"` / `"prune"` / `"fold"` 三个值。`COMPACT_RATIO` 从 0.90 调到 0.85。
  - **新增** `backend/app/services/compact_pipeline.py` — Pipeline 主干 + ToolResultSummarizer + TurnBoundaryFinder + estimate_messages_tokens。
  - **新增** `backend/app/services/compact_markers.py` — CompactMarkerBuilder + CompactSuccessJudge。
- **复用**：`backend/app/services/fs_service.py:extract_outline` / `detect_language` 已存在，阶段 1 直接调用，零额外依赖。
- **DB / API / 事件**：无变更。Tier 0 仍只动 in-memory `messages[]`，不写 DB、不广播事件。`ToolResultEvent` 持久化仍按原样把完整 tool_result 落库。
- **依赖**：无新第三方依赖。`extract_outline` 用 Python `re` 模块，不调 LLM。不引入 tiktoken（content-only 估算 + 阈值补偿已够用）。
- **向后兼容**：
  - 现有 `SOFT_WRAPUP_INSTRUCTION` / `FORCED_FINAL_INSTRUCTION` / `STOP_REASON_LABELS` 不变。
  - `decide_pre_model` 仍返回 `PreModelDecision`，只是 `action` 字段多三个枚举值。
  - `mark_compact_result` 接口不变，仍记 success/failure 累计。
  - 阶段 4/5 行为完全保留，只是触发频率预期下降（前 3 阶段把 ratio 压下去）。
- **测试**：
  - 阶段 1/2/3 各自的剪裁策略单元测试（fs_list / fs_read full / bash / code_explore 等）。
  - TurnBoundaryFinder 边界检测测试（含无 tool_call 的 fallback）。
  - estimate_messages_tokens vs 旧 json.dumps 的偏差测试。
  - judge_compact_success 三态测试（真成功 / 假成功 / 失败）。
  - 端到端：模拟 8 turn × 7 tool = 56 messages 场景，验证阶段 1 在 turn 6 触发、阶段 3 在 turn 8 触发、模型始终看到完整 turn 7-8。
