## 1. 后端 draft 服务工具表同步（Phase 1 - 前置依赖）

- [x] 1.1 在 `backend/app/api/agents.py` 的 `_AVAILABLE_AGENT_TOOLS` 加入 `fs_edit`、`fs_grep`、`fs_glob`（与前端 `AVAILABLE_AGENT_TOOLS` 对齐到 15 个）
- [x] 1.2 在 `_AGENT_TOOL_META` 加入三个工具的 `label`/`desc`（fs_edit→编辑文件、fs_grep→搜索文本、fs_glob→查找文件，与前端一致）
- [x] 1.3 在 `_AGENT_TOOL_PRESETS` 的 `local-code` 预设 `tools` 加入 `fs_edit`、`fs_grep`、`fs_glob`（与前端 local-code 同步）
- [x] 1.4 在 `_ALL_PURPOSE_TOOLS` 加入 `fs_edit`、`fs_grep`、`fs_glob`（与前端 all-purpose 同步，all-purpose 排除 plan_tasks/web_search）
- [x] 1.5 验证 `_normalize_agent_tool_names` 不再过滤这三个工具

## 2. 前端配置层扩展（Phase 2）

- [x] 2.1 在 `src/shared/agent-builder-config.ts` 的 `AgentToolPreset` 接口新增 `systemPromptTemplate: string` 字段
- [x] 2.2 扩展 `AgentToolPresetId` 类型加入 `tech-writing` | `testing-qa` | `frontend-design` | `researcher` | `data-analysis`
- [x] 2.3 新增 5 个 preset 到 `AGENT_TOOL_PRESETS`，每个含 `tools`：
  - `tech-writing`: write_artifact, read_artifact, read_attachment, ask_user, fs_read, fs_list, fs_glob, fs_grep
  - `testing-qa`: bash, fs_read, fs_list, fs_glob, fs_grep, fs_write, read_artifact, ask_user, write_artifact（不含 fs_edit）
  - `frontend-design`: write_artifact, deploy_artifact, read_artifact, ask_user, fs_read, fs_list, fs_glob, fs_grep, fs_write, fs_edit
  - `researcher`: web_search, ask_user, read_attachment, write_artifact, read_artifact
  - `data-analysis`: bash, fs_read, fs_write, fs_list, fs_glob, read_attachment, write_artifact, ask_user
- [x] 2.4 为 9 个角色各写 `systemPromptTemplate`（内容见 `design.md` Appendix，保持 6 条骨架、原则 1/4/5 按角色调整）
- [x] 2.5 扩展 `inferAgentToolPreset` 加入 5 个关键词分支，按特异性排序（先匹配更具体的角色，避免"测试"同时命中 testing-qa 与 local-code）

## 3. 前端布局重构（Phase 3）

- [x] 3.1 在 `src/components/create-agent-dialog.tsx` 的 `toolsPrompt` Tab 重构布局：顶部横向角色条（`flex flex-wrap` 两行折行）+ 下方左右分栏（左工具 / 右 prompt）
- [x] 3.2 角色胶囊选中态高亮（`border-primary bg-primary/5`），点击触发 `applyToolPreset(preset.tools)` + `setSystemPrompt(preset.systemPromptTemplate)`
- [x] 3.3 工具 checkbox 从单列改为多列网格（`grid grid-cols-2`）
- [x] 3.4 `applyToolPreset` 扩展为同时覆盖 prompt：新增 `activePresetId` 状态跟踪当前角色，选胶囊即设 active + 覆盖 prompt
- [x] 3.5 编辑已有 agent 时，初始 `activePresetId` 由 `toolNames` 反推匹配（用 `isPresetActive` 找最匹配的），不覆盖已有 `systemPrompt`
- [x] 3.6 SDK adapter（claude-code/codex）下隐藏角色条与工具 checklist，保留 prompt 编辑区与现有说明文字

## 4. 后端 draft 服务同步（Phase 4）

- [x] 4.1 在 `_AGENT_TOOL_PRESETS` 新增 5 个角色（与前端同步，含 `tools` 与 `label`）
- [x] 4.2 为 5 个新角色在 `_AGENT_TOOL_PRESETS` 加 `systemPromptTemplate` 字段（与前端模板一致）
- [x] 4.3 扩展 `_infer_agent_tool_preset` 加入 5 个关键词分支（与前端 `inferAgentToolPreset` 同步）
- [x] 4.4 `build_heuristic_agent_config_draft` 改为从命中 preset 取 `systemPromptTemplate` 作为 `systemPrompt`（替代当前 `_build_system_prompt` 通用拼装，或保留通用拼装作为 fallback）
- [x] 4.5 验证对话式创建输入 5 类新意图时返回正确 preset + prompt + 工具集

## 5. 规格文档同步（Phase 5）

- [x] 5.1 更新 `specs/10-agent-builder.md` 的角色预设清单（4→9）与"角色选择联动工具+提示词"描述
- [x] 5.2 更新 `specs/09-frontend-architecture.md` 的工具与提示词布局描述（横向角色条 + 左右分栏）

## 6. 端到端验证（Phase 6）

- [x] 6.1 前端：创建 custom agent，依次切 9 个角色，验证工具集 checkbox 与 System Prompt 正确联动覆盖
- [x] 6.2 前端：编辑已有 custom agent，验证初始角色反推正确且不覆盖已持久化的 prompt
- [x] 6.3 前端：切到 Claude Code / Codex adapter，验证角色条与工具 checklist 隐藏、prompt 区保留
- [x] 6.4 前端：窄屏下 9 个角色胶囊折行排布、全部可见可点
- [x] 6.5 后端：对话式创建输入 5 类新意图（技术文档/测试/前端/调研/数据分析），验证 draft 命中正确角色 + prompt + 工具集
- [x] 6.6 后端：验证 `fs_edit`/`fs_grep`/`fs_glob` 在 draft 草稿 `toolNames` 中不再被静默过滤
