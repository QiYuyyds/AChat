## Context

当前角色预设体系在 `src/shared/agent-builder-config.ts`（前端）和 `backend/app/api/agents.py`（后端 draft 服务）各有一份硬编码定义，9 个角色 × 每角色一份 systemPromptTemplate + 一份工具集，内容高度重复（6 条原则骨架中 2/3 逐字相同）。运行时 `agent_runner.py` 还会按工具集合自动注入 `memory_recall`/`rag_*`/`task_dispatch`/`create_plan` 等 10+ 个工具，但 systemPromptTemplate 对这些自动注入工具只字未提，且 `_build_agent_hub_tool_guidance`（第 3 层 prompt）的 `has_file_tools` 块无条件描述 `fs_write`/`fs_edit`，即使 agent 没有这两个工具也给出"正确案例"，造成 LLM 认知混乱——这是"该用的工具没用"现象的根因之一。

用户决策：
1. 工具架构选**选项 1**：10 个工具（read_attachment / ask_user / fs_list / fs_read / fs_write / fs_edit / fs_grep / fs_glob / bash）作为 baseline 必备，所有 custom agent 自带，UI 不可选；工具层面不再做"只读角色"限制，行为约束完全交给 prompt。
2. 角色清单**推翻重设为 4 个**：coder / researcher / orchestrator / writer。
3. systemPromptTemplate 职责**收窄**：只管角色定位 + 产出策略 + 行为约束 + 质量标准，不管工具用法（第 3 层负责）、不管计划/派发引导（第 2 层负责）。
4. Orchestrator 共存策略选**选项 X**：systemPromptTemplate 自成一体讲"协调者定位"，细节由 `_COORDINATED_PROMPT_SUFFIX` 补充。
5. Coder 不默认勾选 `write_artifact`（选项 A），用户需要时手动勾。
6. 旧 agent 由用户手动删除重建，不做数据迁移。

## Goals / Non-Goals

**Goals:**
- 角色预设从 9 个精简为 4 个，每个角色配一份详细的 systemPromptTemplate（定位 + 职责 + 产出策略 + 行为约束 + 质量标准）
- 10 个基础工具 baseline 化，所有 custom agent 必备，UI 不再可选；UI 可选工具缩减为 5 个
- systemPromptTemplate 职责收窄，与第 2/3 层 prompt（loop suffix + tool guidance）职责清晰分离
- 修复 `_build_agent_hub_tool_guidance` 的 `has_file_tools` 块 bug（只描述 agent 实际有的工具）
- 前后端预设定义保持镜像同步

**Non-Goals:**
- 不改 SDK adapter（Claude Code / Codex 用 CLI 内置工具，不参与 baseline 逻辑）
- 不改第 2 层 prompt（`_SOLO_VERIFY_SUFFIX` / `_SOLO_DISPATCH_SUFFIX` / `_PLAN_SUFFIX` / `_COORDINATED_PROMPT_SUFFIX`）的内容
- 不改第 3 层 `_build_agent_hub_tool_guidance` 的整体结构（只修 `has_file_tools` 块的 bug）
- 不做旧 agent 数据迁移（用户手动删除重建）
- 不引入用户自定义预设（预设仍是硬编码）
- 不改 Orchestrator 的 loop 注入逻辑（`task_dispatch` / `dispatch_plan` / `create_plan` 仍由 `_run_coordinated_loop` 自动注入）

## Decisions

### 决策 1：10 个工具 baseline 化，行为约束交给 prompt（选项 1）

`read_attachment` / `ask_user` / `fs_list` / `fs_read` / `fs_write` / `fs_edit` / `fs_grep` / `fs_glob` / `bash` 作为所有 custom agent 的必备工具，UI 不可选。工具层面不再做"只读角色"限制（如原 review 角色不给 fs_write），行为约束完全交给 systemPromptTemplate。

**为什么**：用户观察到"该用的工具没用"的根因之一是某些角色（review / researcher / tech-writing）工具集过窄，需要时拿不到工具。baseline 化后所有 custom agent 能力完整，agent 永远拿得到基础工具。"只读"约束从工具层面退化为 prompt 层面，接受 LLM 偶尔不听 prompt 的风险，换取 agent 能力完整性。

**备选**：
- 选项 2（baseline 分只读/读写两档）：被否决，增加复杂度且"只读"角色实际也常需要写（如审查报告写文件）。
- 选项 3（三档 baseline）：被否决，粒度过细，违背"简化"目标。

**风险缓解**：review 场景的"只读"约束在 writer 角色的"审查报告"场景 prompt 中明确写"不修改被审查的代码或产物；bash 仅用于运行只读检查命令"。LLM 偶尔违反时，用户可在对话中纠正。

### 决策 2：4 个角色划分

| 角色 | 定位 | 覆盖原角色 |
|---|---|---|
| `coder` | 在 workspace 直接改代码、跑命令、验证结果 | local-code + frontend-design + testing-qa 的"写"部分 |
| `researcher` | 联网搜索 + 交叉验证 + 调研报告 | researcher |
| `orchestrator` | 群聊项目经理，拆分派发聚合 | （原无对应，由 is_orchestrator 标志位控制） |
| `writer` | 文字产物交付（技术文档/内容创作/审查报告/网页原型） | artifact + tech-writing + review + data-analysis 的"写"部分 |

**为什么 4 个**：
- `coder` 合并原 local-code + frontend-design + testing-qa：三者本质都是"在 workspace 改代码"，差异在场景侧重（后端/前端/测试），通用 prompt + LLM 自适应足够覆盖，无需 3 份 prompt。
- `writer` 合并原 artifact + tech-writing + review + data-analysis 的"写"部分：四者本质都是"采集信息 → 产出文字产物"，差异在产物类型（文档/文案/报告/原型），用"四类场景侧重"结构在一份 prompt 内覆盖。
- `orchestrator` 独立：协调者职责（拆分/派发/聚合）与其他三者正交，且由 `is_orchestrator` 标志位触发 coordinated loop，需要独立 prompt。
- `researcher` 独立：联网搜索 + 交叉验证是独特能力（需要 web_search），且行为约束（不臆造、标注来源时效）与其他角色差异显著。

**备选**：保留 9 个角色只重写 prompt。被否决，维护成本高（18+ 处硬编码），且 9 个角色划分过细，真实差异有限。

### 决策 3：systemPromptTemplate 职责收窄

systemPromptTemplate 只管 4 件事：
1. **角色定位**（"你是一名 X"）
2. **产出策略**（主要产出物是什么、怎么交付）
3. **行为约束**（角色特有的"该做/不该做"）
4. **质量标准**（什么样的产出算合格）

明确**不管**：
- 具体工具用法（"用 fs_list depth=3"）→ 由 `_build_agent_hub_tool_guidance`（第 3 层）负责
- 多步骤计划引导（"何时建 create_plan"）→ 由 `_PLAN_SUFFIX`（第 2 层）负责
- 子任务派发引导（"何时 task_dispatch"）→ 由 `_SOLO_DISPATCH_SUFFIX` / `_COORDINATED_PROMPT_SUFFIX`（第 2 层）负责
- 工具列表（"你有 write_artifact, read_artifact..."）→ 由 tool definitions（function schema）自带

**为什么**：当前 9 份 template 讲了"多步骤任务先给自己形成简短计划"等与第 2 层 `_PLAN_SUFFIX` 职责重叠的内容，且措辞不一（template 说"简短计划"，suffix 说"调 create_plan"），LLM 收到矛盾信号。职责收窄后，每层 prompt 各司其职，不重叠不冲突。

### 决策 4：Orchestrator 共存策略（选项 X）

Orchestrator 的 systemPromptTemplate 自成一体，讲"协调者定位 + 派发策略 + 聚合质量"，不重复 `_COORDINATED_PROMPT_SUFFIX` 已讲的"task_dispatch / dispatch_plan 使用时机"细节。

**为什么**：systemPromptTemplate 是用户可编辑的，应自成一体让用户单独看也能理解角色。`_COORDINATED_PROMPT_SUFFIX` 是系统注入的，讲工具级细节。两者职责分层：template 讲"我是谁、我该做什么"，suffix 讲"具体工具怎么用"。

### 决策 5：修复 `_build_agent_hub_tool_guidance` 的 has_file_tools 块 bug

当前代码（`agent_runner.py:3449-3485`）只要 agent 有 `fs_list` / `fs_read` / `bash` 中任一个，就无条件输出 `fs_write` / `fs_edit` 的描述和"正确案例"，即使 agent 没有这两个工具。baseline 化后所有 custom agent 都有这两个工具，bug 自然消失；但 SDK agent（Claude Code / Codex）走 CLI 内置工具不参与 baseline，仍需修复此块以避免误导。

**修复方式**：`has_file_tools` 块内的 `fs_write` / `fs_edit` / `bash` 描述行加条件判断（`if "fs_write" in tools or "fs_edit" in tools:`），只描述 agent 实际有的工具。

### 决策 6：UI 可选工具默认勾选矩阵

| 角色 | write_artifact | deploy_artifact | deploy_workspace | read_artifact | web_search |
|---|---|---|---|---|---|
| coder | · | · | ✓ | ✓ | · |
| researcher | ✓ | · | · | ✓ | ✓ |
| orchestrator | ✓ | · | · | ✓ | · |
| writer | ✓ | ✓ | · | ✓ | · |

**为什么**：
- coder 不勾 `write_artifact`（选项 A）：代码直接落盘 workspace，不做成 artifact；用户需要时手动勾。勾 `deploy_workspace`（部署构建产物预览）+ `read_artifact`（读上游 PRD/设计稿）。
- researcher 勾 `web_search`（联网核心能力）+ `write_artifact`（调研报告）+ `read_artifact`（迭代报告）。
- orchestrator 勾 `write_artifact`（聚合报告）+ `read_artifact`（读子任务产物）；不勾 deploy_*（自己不部署）。
- writer 勾 `write_artifact`（文字产物）+ `deploy_artifact`（网页原型预览）+ `read_artifact`（读上游产物迭代）。

## Risks / Trade-offs

- **baseline 化后"只读角色"失去工具层面兜底** → writer 的"审查报告"场景 prompt 明确"不修改被审查的代码或产物；bash 仅用于只读检查命令"；LLM 偶尔违反时用户可在对话中纠正。接受此风险换取 agent 能力完整性。
- **4 个角色覆盖面是否足够** → coder 通用覆盖后端/前端/测试，writer 通用覆盖文档/文案/报告/原型。如果后续发现某场景 LLM 自适应不足，可再拆分（如从 coder 拆出 tester）。当前选择"先简化，按需拆分"。
- **旧 agent 的 toolNames 含 baseline 工具条目** → 运行时合并时去重保留，无害（baseline 工具本来就有）。旧 agent 的 5 个可选工具勾选状态保持原样，用户手动删除重建获得新预设体验。
- **systemPromptTemplate 变长**（4 份各 300-600 字）→ 加上第 2/3 层 suffix，最终 system prompt 约 2000-3000 字。接受此长度换取引导充分性；如发现挤占 context 再精简。
- **前后端预设定义仍两份硬编码** → 本次不根治"两份定义漂移"问题（那是独立的结构性改进）。本次确保两份定义内容镜像同步，漂移风险通过 tasks 中的"同步检查"步骤缓解。
- **`_infer_agent_tool_preset` 关键词推断精度** → 4 个角色的关键词需重新设计，可能与旧 9 角色的用户意图描述不匹配。draft 服务是辅助创建路径，用户可切到详细配置调整，推断精度不阻断核心流程。

## Migration Plan

1. **无需数据迁移**：旧 agent 的 `toolNames` 保持原样，baseline 合并逻辑自动生效。用户手动删除旧 agent 重建获得新预设。
2. **部署顺序**：前后端可同步部署（baseline 合并逻辑在后端，UI 变更在前端，互不阻塞）。
3. **回滚策略**：回滚前端 UI 变更只需还原 `agent-builder-config.ts` + `create-agent-dialog.tsx`；回滚后端 baseline 合并只需还原 `agent_runner.py` 的工具拼装逻辑。两端口契约（`toolNames` 字段语义）不变，可独立回滚。

## Open Questions

无。所有关键决策已在探索阶段与用户确认。
