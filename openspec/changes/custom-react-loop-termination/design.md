## Context

AChat 的 Custom（OpenAI 兼容 SDK）路径由应用层拥有 ReAct 循环（`custom_adapter` / `agent_runner` 中的 SDK react 分支）。当前默认 `MAX_TURNS = 8` / `REACT_LOOP_MAX_TURNS = 8`，长任务被硬截断且常静默结束。CLI 路径（Claude Code / Codex）由 vendor 子进程 loop 结束，本变更不触及。

编排层（`run_agent_loop` solo/coordinated/subagent、`task_dispatch` / `dispatch_plan`）只负责工具与 system prompt 注入，最终仍进入 `execute_simple_run`。终止语义应落在 **Custom 执行环**，不改编排模式语义。

已有能力可复用：mid-run compact（约 90% tokens）、高水位停止（约 95%）、跨 run auto-compact hook、`cancel_event`。本次把它们收成 **一条** 收尾状态机，并补 soft/forced 与行为断路器。

Explore 定稿约束：
1. 不要产品级默认 max steps；结束像 Claude Code / Codex（model-done 主路径）
2. 只改 Custom
3. 尽可能不改编排；必须改时先问人（本设计选择不改编排）

## Goals / Non-Goals

**Goals:**

- Custom 主路径以 **0 tool call** 结束
- 去掉默认 8 步硬帽；可选超高 `max_tool_turns` 作最后保险丝（默认关）
- 统一 **compact → soft wrap-up → forced final → hard stop** 管线
- soft 注入对用户不可见；forced 输出面向用户的自然语言总结
- 重复 tool 指纹 / 同 tool 连败 / compact 连败断路器
- 内部 `stop_reason` + 用户轻提示
- 子 Custom run 非正常结束时，tool result 可辨识（不改编排框架）
- 测试与 eval 与新语义对齐

**Non-Goals:**

- 修改 Claude Code / Codex adapter loop
- 修改 dispatch/DAG/plan 编排语义或 `MAX_DISPATCH_DEPTH`
- `/goal`、独立 verifier、Ralph 外环
- soft 轮只读 tool 裁剪
- forced 强制 JSON schema
- 新第三方依赖

## Decisions

### D1. 终止哲学：model-done 主路径，无默认 max steps

- **选择**：删除/停用产品默认 `MAX_TURNS=8`；循环在「无 tool call」「cancel」「收尾管线完成」「可选 max_tool_turns 收尾」时退出。
- **备选**：把 8 改成 90/Hermes 默认 — 仍是步数主控，长尾任务误杀与假安全感并存。
- **理由**：对齐 Claude Code（`max_turns` 默认无限）与 Codex（assistant message 结束）。

### D2. 收尾管线顺序：compact → soft → forced → hard

| 阈值（相对 context 上限，可配置常量） | 行为 |
|--------------------------------------|------|
| ~90% | mid-run compact（续命，不是收尾） |
| ~92–93% | soft wrap-up：隐藏注入收工指令；**仍挂全量 tools** |
| soft 后再发 tool / 断路器触发 | forced final：**恰好 1 次** `tools=[]`（或忽略 tool_call），用户向自然语言 |
| ~95% 或 forced 之后 | hard：不再开启新的 tool 执行轮 |

- **选择**：阈值顺序 A（compact 后再 soft），不用「更早 soft」或「与 hard 同点」。
- **备选**：85% 就 soft — Hermes 经验表明中途压力易导致 early abandon。
- **实现要点**：
  - 在 **每一轮 model 调用前** 评估 token 占比与断路器，避免大 generation 跳过 soft。
  - 若已 ≥ hard 阈值但仍未做过 soft/forced，**仍保证 soft（若适用）与 forced 各至多一次**，再 hard stop。
  - 与现有 `_mid_run_compact` / 95% 逻辑 **合并为同一状态机**，禁止并行两套 if。

### D3. Soft 失败策略 C：soft 后仍 tool → forced

- Soft：只劝不绑（文案 + 全量 tools）。
- Forced：硬约束无 tools，保证可读终态。
- Soft 注入消息：`hidden` / 不进用户消息列表 / 不进 `build_history_for` 的用户可见投影（实现可选 internal system 段或 hidden message part 策略，以「用户气泡不可见、模型可见」为准）。

### D4. Forced 文案：面向用户自然语言

模板要点（实现可常量化）：已完成、未完成、阻塞/风险、建议下一步；禁止假装任务已 100% 完成（若因预算/断路器停止）。

建议 harness **附带短事实摘要**（本 run 调用过的 tool 名、失败次数、断路原因）注入 forced 轮，降低 compact 后空总结概率。

### D5. 可选 `max_tool_turns`

- 默认：`None` / 关闭（叙事上「无默认步数帽」）。
- 若配置了正整数 N：tool 轮计数达到 N 时进入 **与 token 相同的 soft → forced 管线**，用户轻提示「达到操作轮次上限」。
- 不默认写成 200 以免被当成软 max steps。

### D6. 行为断路器

| 断路器 | 条件 | 动作 |
|--------|------|------|
| 重复调用 | 相同 `tool_name` + **稳定参数指纹** 连续 3 次 | 第 3 次后注入换策略；若下一轮仍同指纹 → forced |
| 同 tool 失败 | 同一 `tool_name` 连续执行失败 ≥ 3 | 注入；再失败 → forced |
| Compact 失败 | mid-run compact 连续失败 ≥ 3 | 停止再 compact，直接进 soft→forced |

**指纹规则（防误杀优先）**：

- 规范化：tool 名；参数 JSON 以排序 key 序列化；路径做 normpath/相对 workspace 形式（若可得）。
- **排除**明显易变字段（timestamp、uuid、request_id 等）——可用 denylist 或仅对已知 tool schema 白名单字段哈希。
- 不确定时宁可漏检，不误杀合法重试。

### D7. `stop_reason` 与 UI

内部枚举（建议，实现可微调命名）：

- `complete` — model-done
- `cancelled` — cancel_event
- `budget_soft_complete` — soft 后 0 tool 结束
- `budget_forced_final` — 走了 forced
- `budget_exhausted` — hard/异常，无有效终稿
- `duplicate_tool_breaker`
- `tool_error_breaker`
- `compact_failure_breaker`
- `max_tool_turns`

用户：映射为短中文轻提示（B）；不展示原始枚举。  
Soft 注入句：不可见（A）。

事件：在现有 run 结束事件（如 `run.end` / usage 伴随结构）上增加 camelCase 字段，例如 `stopReason` + `stopReasonLabel`；前端 store 读取后展示。

### D8. 范围：Custom only，编排不动

- `SDK_ADAPTERS` / Custom react 路径启用本状态机。
- CLI adapter 保持 vendor 完成语义。
- `agent_loop.py` 仅在需要时更新注释/交叉引用；不改 mode 工具注入逻辑。
- 子 run：各自跑 Custom 状态机；`spawn_subagent_loop` 返回的 summary **前缀或字段**标明非 `complete`（例如 `[stopped: budget_forced_final]`），便于父模型理解——属 tool result 字符串约定，非编排 API 变更。

### D9. 代码落点（建议）

| 区域 | 职责 |
|------|------|
| `agent_runner` ReAct 状态机 | 轮询前门槛、soft/forced 调度、stop_reason、与 compact 合并 |
| `custom_adapter` | 去掉默认 8；支持「本轮 tools 为空」的 stream；轮次计数可上移到 runner 统一 |
| 事件 / schema | `stopReason` / `stopReasonLabel` |
| 前端 run/消息 UI | 轻提示 |
| `eval_rules` | 移除或重写基于 MAX_TURNS=8 的判定 |
| 测试 | `test_react_loop` / `test_custom_adapter` / compact 相关 + 新断路器用例 |

### D10. 与 Claude / Codex / Hermes 的对齐关系

- 主路径 model-done：三家共识 / Claude+Codex 默认无小步数帽
- soft wrap-up 文案：最接近 Codex goal `budget_limit.md`
- forced 无-tools：比三家公开主路径更强的 Custom 兜底
- 忌过早 soft：吸收 Hermes 拿掉 mid-budget pressure 的教训
- compact 失败断路：吸收 Claude autocompact 连败 cap 思路

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| 大 generation 跳过 92–93% 窗口 | 每轮 model 调用前检查；保证 soft/forced 机会 |
| 指纹误杀合法重试 | 稳定字段 only；宁可漏杀；阈值 3 |
| Forced 总结空洞 | harness 注入 run 事实摘要 |
| 与旧 90/95 逻辑双轨 | 单状态机重构，删重复分支 |
| 删 8 后 runaway 烧钱 | 断路器 + token hard + 可选 max_tool_turns + cancel |
| 子 run 被 forced 父不知 | tool result 带 stop 说明 |
| eval 假失败 | 同步改 `eval_rules` 与测试 |
| 事件字段扩展兼容 | 可选字段；旧前端忽略即可 |

**Trade-off**：比「只改 8→N」实现量大，但正确解决误杀与静默结束；v1 不做 goal/verifier 以控制范围。

## Migration Plan

1. 合并代码：默认关闭步数帽；上线状态机与事件字段。
2. 前端兼容：无 `stopReasonLabel` 时不展示轻提示（退化为现状）。
3. 文档：`specs/05-adapter-interface.md` 去掉 MAX_TURNS=8 作为正常契约；OpenSpec archive 时合并 capability。
4. 回滚：feature flag（可选）`custom_loop_termination_v2`——若需快速回滚可恢复步数帽；**默认可不加 flag** 若团队接受直接切换（实现期决定）。推荐至少保留配置项 `max_tool_turns` 与阈值常量便于调参。
5. 观察：run 的 stop_reason 分布、forced 率、平均 tool 轮次。

## Open Questions

- 精确百分比常量（90 / 93 / 95）是否做成 settings 可配，还是代码常量即可？（建议 v1 代码常量 + 注释，避免 settings 膨胀）
- `stopReason` 挂在 `run.end` 还是 `run.usage` 旁路字段？（实现时以现有事件模型最小改动为准）
- 是否需要 feature flag？（非阻塞；可在 tasks 中列为可选）
