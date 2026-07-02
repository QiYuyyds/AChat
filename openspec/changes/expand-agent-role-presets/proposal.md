## Why

custom agent 创建表单当前只有 4 个角色预设（全栈通用、产物交付、本地代码、审查验证），且角色选择仅切换工具集、不联动系统提示词——用户选完角色仍面对一个空 System Prompt。工具与提示词的页面布局是上下堆叠式（预设按钮 2 列 + 15 个工具 checkbox 垂直排 + prompt 在最下方），操作链路长、角色少、覆盖面窄。

实际项目场景远超 4 类：技术写作、测试 QA、前端/设计、联网调研、数据分析都是高频需求，但用户只能从零手填 prompt + 逐个勾工具。此外，后端 draft 服务（对话式创建）的工具表与前端不同步——后端 `_AVAILABLE_AGENT_TOOLS` 缺 `fs_edit`/`fs_grep`/`fs_glob`，导致草稿会静默丢失这 3 个工具。

## What Changes

- **角色预设从 4 个扩展到 9 个**：新增技术写作（`tech-writing`）、测试 QA（`testing-qa`）、前端/设计（`frontend-design`）、调研员（`researcher`）、数据分析（`data-analysis`）。
- **`AgentToolPreset` 接口新增 `systemPromptTemplate` 字段**：每个角色绑定一份基于通用骨架定制的系统提示词模板（定位句 + 工作原则 6 条，原则 1/4/5 按角色侧重调整）。
- **角色选择改为自动覆盖联动**：选角色即时切换工具集 checkbox + 用该角色模板覆盖 System Prompt（用户仍可手动微调工具与 prompt）。
- **`toolsPrompt` Tab 布局重构**：横向角色条（两行折行排布，非横向滚动）+ 左右分栏（左：工具 checkbox 多列网格，右：System Prompt 编辑区）。
- **后端 draft 服务同步**：补齐 `fs_edit`/`fs_grep`/`fs_glob` 到 `_AVAILABLE_AGENT_TOOLS` + `_AGENT_TOOL_META` + `local-code` 预设；新增 5 个角色 preset + `_infer_agent_tool_preset` 关键词分支 + 每角色 prompt 模板。
- **仅影响 custom agent**：Claude Code / Codex 等 SDK agent 使用各自 CLI 内置工具集，其提示词体系后续单独设计，不参与本次角色预设逻辑。

## Capabilities

### New Capabilities

无。所有变更都是增强现有 `agent-builder` 与 `frontend` 两个 capability 的 requirements。

### Modified Capabilities

- `agent-builder`：角色预设从 4 个扩展到 9 个；preset 不再只有工具集，而是「工具集 + 系统提示词模板」的配置包；角色选择联动方式从「仅切工具」升级为「切工具 + 覆盖 prompt」。
- `frontend`：创建/编辑 Agent 弹窗的「工具与提示词」Tab 布局从上下堆叠改为横向角色条 + 左右分栏。

## Impact

- **前端**：`src/shared/agent-builder-config.ts` 扩展 `AgentToolPreset` 接口（加 `systemPromptTemplate`）、新增 5 个 preset、为 9 个角色各写 prompt 模板、`inferAgentToolPreset` 扩展 5 个关键词分支；`src/components/create-agent-dialog.tsx` 的 `toolsPrompt` Tab 重构为横向角色条 + 左右分栏 + 自动覆盖联动。
- **后端**：`backend/app/api/agents.py` 的 draft 服务镜像同步——补齐 3 个工具到 `_AVAILABLE_AGENT_TOOLS`/`_AGENT_TOOL_META`/`local-code` 预设，新增 5 个角色到 `_AGENT_TOOL_PRESETS` + `_infer_agent_tool_preset` + `_build_system_prompt` 按角色选模板。
- **规格文档**：`specs/10-agent-builder.md` 更新角色预设清单与联动描述；`specs/09-frontend-architecture.md` 更新工具与提示词布局描述。
- **测试**：前端验证 9 个角色切换的工具集 + prompt 覆盖；后端验证 draft 服务对 5 类新意图的 preset 推断与 prompt 生成。
- **兼容性**：已存在 custom agent 的 `toolNames` 与 `systemPrompt` 已持久化在 DB，不受预设变更影响；仅新建 agent 或用户重新选预设时才应用新模板。后端补齐 3 工具是纯增量，不影响已注册工具行为。
