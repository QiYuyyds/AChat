## 1. 前端 agent-builder-config.ts 重写

- [x] 1.1 新增 `BASELINE_AGENT_TOOLS` 常量（9 个工具：read_attachment, ask_user, fs_list, fs_read, fs_write, fs_edit, fs_grep, fs_glob, bash），并新增 `BASELINE_AGENT_TOOL_META` 为这 9 个工具提供 label/desc（用于 UI 展示提示）
- [x] 1.2 将 `AVAILABLE_AGENT_TOOLS` 从 14 个缩减为 5 个（write_artifact, deploy_artifact, deploy_workspace, read_artifact, web_search）；`AGENT_TOOL_META` 移除 baseline 工具条目，只保留这 5 个
- [x] 1.3 重写 `AGENT_TOOL_PRESETS` 为 4 个角色（coder / researcher / orchestrator / writer），每个 preset 的 `tools` 字段只包含 5 个可选工具的子集，`systemPromptTemplate` 使用 design 中确定的 4 份详细 prompt
- [x] 1.4 重写 `AgentToolPresetId` 类型为 `'coder' | 'researcher' | 'orchestrator' | 'writer'`
- [x] 1.5 重写 `inferAgentToolPreset` 关键词分支为 4 角色（coder 关键词：代码/实现/bug/重构/测试/前端/后端；researcher：调研/搜索/竞品/选型；orchestrator：协调/派发/项目管理；writer：文档/文案/报告/原型/审查）
- [x] 1.6 修改 `normalizeAgentToolNames` 只过滤 5 个可选工具（baseline 工具不在过滤集，由后端合并）
- [x] 1.7 修改 `DEFAULT_CUSTOM_AGENT_TOOLS` 为 coder preset 的 tools（`['deploy_workspace', 'read_artifact']`）

## 2. 前端 create-agent-dialog.tsx UI 调整

- [x] 2.1 修改 `toolsPrompt` Tab 的工具勾选区：从 14 个 checkbox 改为 5 个 checkbox（只展示 `AVAILABLE_AGENT_TOOLS`），上方新增"所有 custom agent 自带以下基础工具"提示区（只读，列出 `BASELINE_AGENT_TOOLS` 及其 label/desc）
- [x] 2.2 修改角色预设按钮区：从 9 个按钮改为 4 个（coder / researcher / orchestrator / writer）
- [x] 2.3 修改 `applyToolPreset` 逻辑：切换 preset 时只覆盖 5 个可选工具的勾选状态（`setToolNames(new Set(preset.tools))`），baseline 工具不在 Set 里（因为不可选）
- [x] 2.4 修改编辑模式回填逻辑：从 persisted `toolNames` 推断 activePresetId 时，只匹配 5 个可选工具的子集（过滤掉 baseline 工具后再匹配）
- [x] 2.5 修改创建态默认值：`setToolNames(new Set(DEFAULT_CUSTOM_AGENT_TOOLS))` + `setActivePresetId('coder')` + `setSystemPrompt(DEFAULT_CUSTOM_SYSTEM_PROMPT)`（指向 coder template）
- [x] 2.6 验证 SDK adapter（claude-code / codex）的 tools tab 不显示 baseline 提示和 5 个 checkbox（保持原有"CLI 内置工具"提示）

## 3. 后端 agents.py draft 服务镜像同步

- [x] 3.1 新增 `_BASELINE_AGENT_TOOLS` 常量（9 个工具，与前端一致）
- [x] 3.2 将 `_AVAILABLE_AGENT_TOOLS` 从 14 个缩减为 5 个；`_AGENT_TOOL_META` 移除 baseline 条目，只保留 5 个
- [x] 3.3 重写 `_AGENT_TOOL_PRESETS` 为 4 个角色，`systemPromptTemplate` 使用与前端一致的 4 份 prompt
- [x] 3.4 重写 `_infer_agent_tool_preset` 关键词分支为 4 角色
- [x] 3.5 重写 `_build_system_prompt`：风格对齐新 template（定位 + 职责 + 产出策略 + 约束 + 质量），不再拼装工具权限清单（baseline 自动有，无需列举）
- [x] 3.6 修改 `_normalize_agent_tool_names` 只过滤 5 个可选工具
- [x] 3.7 修改 `_infer_agent_name` / `_infer_description` / `_infer_capabilities` 的 preset 映射表为 4 角色
- [x] 3.8 修改 `build_heuristic_agent_config_draft` 的默认 preset 为 coder

## 4. 后端 agent_runner.py baseline 合并 + bug 修复

- [x] 4.1 在 `execute_simple_run` 的工具拼装逻辑中，对 custom adapter agent 合并 baseline：`base_tool_names = list(dict.fromkeys(_BASELINE_AGENT_TOOLS + configured))`（仅 custom adapter，SDK adapter 跳过）
- [x] 4.2 修复 `_build_agent_hub_tool_guidance` 的 `has_file_tools` 块 bug：`fs_write` / `fs_edit` / `bash` 的描述行和"正确案例"行加 `if "fs_write" in tools` 等条件判断，只描述 agent 实际有的工具
- [x] 4.3 验证 baseline 合并对旧 agent 的兼容性：旧 agent 的 `toolNames` 含 baseline 工具时去重保留（`dict.fromkeys` 保序去重），5 个可选工具的勾选状态保持原样
- [x] 4.4 验证 SDK agent（claude-code / codex）不参与 baseline 合并（保持原有 `tool_names_list=[]` + CLI 内置工具逻辑）

## 5. 规格文档同步

- [x] 5.1 更新 `specs/10-agent-builder.md`：角色预设清单从 9 个改为 4 个（coder/researcher/orchestrator/writer）；工具架构从"14 个 UI 可选"改为"9 个 baseline + 5 个 UI 可选"；systemPromptTemplate 职责定义收窄；可配置字段表的 `toolNames` 说明更新
- [x] 5.2 更新 `openspec/specs/agent-builder/spec.md`：requirements 与 delta spec 对齐
- [x] 5.3 更新 `openspec/specs/frontend/spec.md`：requirements 与 delta spec 对齐

## 6. 测试与验证

- [x] 6.1 前端：验证 4 个角色预设按钮切换时，5 个 checkbox 勾选状态和 System Prompt 正确联动
- [x] 6.2 前端：验证 baseline 工具提示区正确展示 9 个工具的 label/desc，且不可勾选
- [x] 6.3 前端：验证编辑旧 agent 时，activePresetId 推断正确（只匹配 5 个可选工具），persisted systemPrompt 不被覆盖
- [x] 6.4 前端：验证 SDK adapter 的 tools tab 不显示 baseline 提示和 checkbox
- [x] 6.5 前端：`pnpm typecheck` 过（修改文件无新错误；9 个 pre-existing 错误在未修改文件中）
- [x] 6.6 前端：`pnpm lint` 过（修改文件 `agent-builder-config.ts` + `create-agent-dialog.tsx` 无 lint 错误）
- [x] 6.7 后端：验证 draft 服务对 4 类意图（代码/调研/协调/写作）的 preset 推断正确
- [x] 6.8 后端：验证 baseline 合并逻辑——custom agent runtime tool list = baseline + toolNames + auto-injected，去重正确
- [x] 6.9 后端：验证 `_build_agent_hub_tool_guidance` 的 has_file_tools 块只描述 agent 实际有的工具（条件判断已加）
- [x] 6.10 后端：`ruff check .` 过（修改文件无新错误；15 个 pre-existing 错误在未修改代码中）
- [x] 6.11 后端：`pytest` 过（29 passed + 8 passed；1 个 pre-existing DB engine config 错误与本次修改无关）
