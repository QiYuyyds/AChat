## Why

当前 9 个角色预设存在三个结构性问题：(1) 工具架构让"应该必备的工具"（fs_read/fs_write/bash/ask_user 等 10 个）变成 UI 可选项，导致某些角色（如 review、researcher）在需要时拿不到工具，agent 行为不符合预期；(2) systemPromptTemplate 职责越界，既讲角色定位又讲具体工具用法，与第 2/3 层 prompt（loop suffix + `_build_agent_hub_tool_guidance`）职责重叠且风格不一；(3) 9 个角色划分过细，prompt 共享 6 条骨架但真正差异化的内容有限，维护成本高（前后端各 9 份 + 工具表两份硬编码，改一处要改 18+ 处）。此外 `_build_agent_hub_tool_guidance` 的 `has_file_tools` 块无条件描述 `fs_write`/`fs_edit`，即使 agent 没有这两个工具也给出"正确案例"，造成 LLM 认知混乱。

## What Changes

- **BREAKING**（预设架构）：角色预设从 9 个（all-purpose / local-code / artifact / review / tech-writing / testing-qa / frontend-design / researcher / data-analysis）**推翻重设为 4 个**：`coder`（程序员）、`researcher`（调研员）、`orchestrator`（协调者）、`writer`（写作）。
- **BREAKING**（工具架构）：引入 `BASELINE_AGENT_TOOLS` 概念——以下 10 个工具成为所有 custom agent 的必备工具，**从 UI 可选项中移除**：`read_attachment`、`ask_user`、`fs_list`、`fs_read`、`fs_write`、`fs_edit`、`fs_grep`、`fs_glob`、`bash`。
- UI 可选工具从 14 个缩减为 5 个：`write_artifact`、`deploy_artifact`、`deploy_workspace`、`read_artifact`、`web_search`。角色间的工具差异仅体现在这 5 个上。
- 运行时工具拼装逻辑改为：`effective_tools = BASELINE_AGENT_TOOLS + agent.tool_names + 自动注入工具`。`agent.tool_names` 现在只存"增量工具"（5 个 UI 可选项的子集）。
- **systemPromptTemplate 职责收窄**：只管"角色定位 + 产出策略 + 行为约束 + 质量标准"，不再讲具体工具用法（工具用法由 `_build_agent_hub_tool_guidance` 负责）、不讲"多步骤计划"（由 `_PLAN_SUFFIX` 负责）、不讲"子任务派发"（由 `_SOLO_DISPATCH_SUFFIX` / `_COORDINATED_PROMPT_SUFFIX` 负责）。
- 4 个角色各配一份详细的 systemPromptTemplate（定位 + 职责 + 产出策略 + 行为约束 + 质量标准），内容比原 6 条骨架更具体、更有引导性。
- **修复 `_build_agent_hub_tool_guidance` 的 `has_file_tools` 块 bug**：只描述 agent 实际拥有的工具，不再无条件列出 `fs_write`/`fs_edit` 的"正确案例"。
- 后端 `backend/app/api/agents.py` 的 draft 服务（对话式创建）与前端镜像同步：`_AVAILABLE_AGENT_TOOLS` 缩减为 5 个、`_AGENT_TOOL_PRESETS` 重写为 4 个、`_infer_agent_tool_preset` 关键词分支重写、`_build_system_prompt` 风格对齐新 template。
- **仅影响 custom agent**：Claude Code / Codex 等 SDK adapter 使用各自 CLI 内置工具集，不参与 baseline 逻辑。

## Capabilities

### New Capabilities

无。所有变更都是修改现有 `agent-builder` 与 `frontend` 两个 capability。

### Modified Capabilities

- `agent-builder`：角色预设从 9 个重设为 4 个（coder / researcher / orchestrator / writer）；引入 `BASELINE_AGENT_TOOLS`（10 个工具对所有 custom agent 必备，UI 不可选）；UI 可选工具从 14 个缩减为 5 个；`AgentToolPreset.systemPromptTemplate` 职责收窄为"角色定位 + 产出策略 + 行为约束 + 质量标准"，不再讲工具用法与计划/派发引导；4 份 template 内容重写，比原 6 条骨架更详细。
- `frontend`：创建/编辑 Agent 弹窗的「工具与提示词」Tab 工具勾选区从 14 个 checkbox 缩减为 5 个（baseline 10 个不再展示为可勾选项，改为提示"所有 custom agent 自带以下基础工具"）；角色预设按钮从 9 个改为 4 个。

## Impact

- **前端**：`src/shared/agent-builder-config.ts` 重写——`AVAILABLE_AGENT_TOOLS` 从 14 个改为 5 个、新增 `BASELINE_AGENT_TOOLS` 常量（10 个）、`AGENT_TOOL_PRESETS` 从 9 个重写为 4 个、`AGENT_TOOL_META` 移除 baseline 工具的条目（保留 5 个可选工具的 meta）、`inferAgentToolPreset` 关键词分支重写为 4 角色、`normalizeAgentToolNames` 改为只过滤 5 个可选工具（baseline 由后端合并）；`src/components/create-agent-dialog.tsx` 的工具勾选区 UI 调整（展示 baseline 提示 + 5 个可选 checkbox）、角色预设按钮从 9 个改为 4 个、`applyToolPreset` 逻辑调整（只覆盖 5 个可选工具的勾选，不动 baseline）。
- **后端**：`backend/app/api/agents.py` 的 draft 服务镜像同步——`_AVAILABLE_AGENT_TOOLS` 改为 5 个、新增 `_BASELINE_AGENT_TOOLS` 常量、`_AGENT_TOOL_PRESETS` 重写为 4 个、`_infer_agent_tool_preset` 关键词分支重写、4 份 prompt 模板替换、`_build_system_prompt` 风格对齐；`backend/app/services/agent_runner.py` 的 `execute_simple_run` 工具拼装逻辑加 baseline 合并（`BASELINE_AGENT_TOOLS + agent.tool_names_list`）、修复 `_build_agent_hub_tool_guidance` 的 `has_file_tools` 块只描述 agent 实际有的工具。
- **规格文档**：`specs/10-agent-builder.md` 更新角色预设清单（4 个）、工具架构（baseline + 可选）、systemPromptTemplate 职责定义；`openspec/specs/agent-builder/spec.md` 同步更新 requirements。
- **兼容性**：已存在 custom agent 的 `toolNames` 已持久化，按旧 9 角色存。新架构下 baseline 10 个工具自动合并生效，旧 `toolNames` 中的 baseline 工具条目会被去重保留（无害），5 个可选工具的勾选状态保持原样。用户手动删除旧 agent 重建即可获得新预设体验。`_build_agent_hub_tool_guidance` 的 bug 修复对所有 agent 生效（只描述实际有的工具）。
- **测试**：前端验证 4 个角色切换的工具集 + prompt 覆盖；后端验证 draft 服务对 4 类意图的 preset 推断；验证 baseline 合并逻辑（旧 agent toolNames 含 baseline 工具时不重复）。
