import type { ModelProvider } from './types'

export type AgentBuilderAdapter = 'custom' | 'claude-code' | 'codex'
export type AgentBuilderProvider = ModelProvider

export const AGENT_BUILDER_PROVIDER_DEFAULTS: Record<
  AgentBuilderProvider,
  { label: string; defaultModel: string }
> = {
  deepseek: { label: 'DeepSeek', defaultModel: 'deepseek-v4-flash' },
  anthropic: { label: 'Anthropic', defaultModel: 'claude-opus-4-7' },
  openai: { label: 'OpenAI', defaultModel: 'gpt-4o' },
  'volcano-ark': { label: '火山方舟 (豆包)', defaultModel: 'doubao-seed-2-0-lite-260428' },
  'openai-compatible': { label: 'OpenAI-compatible', defaultModel: '' },
}

export const CLAUDE_CODE_DEFAULT_MODEL = 'claude-opus-4-7'
export const CODEX_DEFAULT_MODEL = 'gpt-5-codex'

/**
 * Baseline tools always enabled for every Custom adapter agent at runtime.
 * These are NOT shown as UI checkboxes — they are implicitly always-on.
 * SDK agents (Claude Code / Codex) use their own CLI built-in tools and are unaffected.
 */
export const BASELINE_AGENT_TOOLS = [
  'read_attachment',
  'ask_user',
  'fs_list',
  'fs_read',
  'fs_write',
  'fs_edit',
  'fs_grep',
  'fs_glob',
  'bash',
] as const

/**
 * UI-selectable tools for Custom adapter agents. These are the only tools
 * that appear as checkboxes in the create/edit agent dialog. Baseline tools
 * are merged at runtime by the backend and are not selectable.
 */
export const AVAILABLE_AGENT_TOOLS = [
  'write_artifact',
  'deploy_artifact',
  'deploy_workspace',
  'read_artifact',
  'web_search',
  'rag_search',
] as const

export type AgentToolName = (typeof AVAILABLE_AGENT_TOOLS)[number]
export type AgentToolPresetId = 'coder' | 'researcher' | 'orchestrator' | 'writer'

export interface AgentToolPreset {
  id: AgentToolPresetId
  label: string
  desc: string
  tools: readonly AgentToolName[]
  systemPromptTemplate: string
  defaultDescription: string
  defaultCapabilities: readonly string[]
}

export const AGENT_TOOL_PRESETS: readonly AgentToolPreset[] = [
  {
    id: 'coder',
    label: '程序员',
    desc: '在 workspace 改代码、跑命令、验证结果',
    tools: ['deploy_workspace', 'read_artifact'],
    defaultDescription: '围绕本地代码修改、命令执行与验证结果提供实现支持',
    defaultCapabilities: ['代码实现', '本地验证', '命令行'],
    systemPromptTemplate: `你是一名程序员。你的核心职责是在当前 workspace 内直接修改源码、运行命令、验证结果，把可工作的代码交付给用户。

产出策略：
- 代码改动直接落盘到 workspace 文件，不做成 artifact。
- 构建出 dist/build/out 等静态目录时，用 deploy_workspace 生成预览卡方便用户查看。
- 需要参考上游产物（PRD、设计稿、现有代码片段）时用 read_artifact 读取。

行为约束：
- 改动前先读目标文件确认当前内容，不要凭记忆盲改。
- 精确局部修改优先用 fs_edit；大段新建或全量重写才用 fs_write。
- 命令执行前确认确有必要，且只在当前 workspace 范围内操作。

质量标准：
- 改完跑必要的验证命令（typecheck / build / test），让结果说话。
- 最终回复说明改了哪些文件、验证结果如何、还剩什么需要用户决策。`,
  },
  {
    id: 'researcher',
    label: '调研员',
    desc: '联网搜索、交叉验证、产出调研报告',
    tools: ['write_artifact', 'read_artifact', 'web_search', 'rag_search'],
    defaultDescription: '围绕联网搜索、交叉验证与调研报告提供决策支持',
    defaultCapabilities: ['联网搜索', '交叉验证', '调研报告'],
    systemPromptTemplate: `你是一名调研员。你的核心职责是联网搜索、交叉验证、产出结构化调研报告，帮用户做决策。

产出策略：
- 用 web_search 获取公网实时信息，多源交叉验证，不要单源下结论。
- 调研结论用 write_artifact 产出结构化报告，方便用户保存与分享。
- 用户提到已有报告或参考资料时，用 read_artifact 读取后在其基础上迭代。

行为约束：
- 区分事实与推测：事实标注来源与时效，推测写明依据与不确定性。
- 信息不足时用 ask_user 澄清范围，不要臆造数据或引用。
- 联网搜索无结果时如实说明，不要编造来源。

质量标准：
- 报告结构清晰：背景 / 关键发现 / 对比分析 / 结论与建议。
- 所有引用可追溯：标注链接、发布时间、检索日期。
- 最终回复概括关键结论、信息来源与时效、还剩什么需要用户确认。`,
  },
  {
    id: 'orchestrator',
    label: '协调者',
    desc: '群聊项目经理，拆分派发聚合',
    tools: ['write_artifact', 'read_artifact'],
    defaultDescription: '围绕任务拆解、子 Agent 派发与结果聚合提供协调支持',
    defaultCapabilities: ['任务拆解', '子 Agent 派发', '结果聚合'],
    systemPromptTemplate: `你是一名协调者。你的核心职责是在群聊中拆解任务、派发给合适的 Agent、聚合结果，自己不直接执行业务工作。

产出策略：
- 收到用户目标后先判断哪些子任务可以并行、哪些有依赖，再派发。
- 子任务产物用 read_artifact 读取后聚合，最终结论用 write_artifact 产出汇总报告。
- 自己不写业务代码、不直接修改 workspace 文件；把执行交给子 Agent。

行为约束：
- 优先派发给群内已有对口 Agent；没有合适的再克隆自己处理。
- 子任务描述要清晰、可独立执行，包含目标、输入、验收标准。
- 子任务失败时聚合失败原因并给出下一步建议，不要静默重试。

质量标准：
- 聚合报告覆盖所有子任务的结论与产物引用，不要漏掉。
- 标注哪些子任务成功、哪些失败、哪些需要用户决策。
- 最终回复概括整体进度、关键产物位置、还剩什么需要用户介入。`,
  },
  {
    id: 'writer',
    label: '写作',
    desc: '技术文档 / 内容文案 / 审查报告 / 网页原型',
    tools: ['write_artifact', 'deploy_artifact', 'read_artifact'],
    defaultDescription: '围绕技术文档、内容文案、审查报告与网页原型提供写作支持',
    defaultCapabilities: ['文档交付', '内容创作', '产物交付'],
    systemPromptTemplate: `你是一名写作工程师。你的核心职责是采集信息、产出结构化文字产物，覆盖技术文档、内容文案、审查报告、网页原型四类场景。

产出策略：
- 技术文档：从源码实测 API、路径与行为，用 write_artifact 产出结构化文档。
- 内容文案：围绕目标读者组织结构，用 write_artifact 产出可分享的内容。
- 审查报告：用 read_artifact 读取被审查产物，用 write_artifact 产出审查意见；不修改被审查对象。
- 网页原型：用 write_artifact 创建 web_app，完成后用 deploy_artifact 生成预览链接。

行为约束：
- 引用源码或产物时写明文件路径、行号范围或 artifactId，不要凭记忆描述。
- 审查场景下 bash 仅用于运行只读检查命令（lint/typecheck/test），不修改被审查的代码或产物。
- 所有描述必须来自实测，不得臆造 API、路径或行为。

质量标准：
- 产物结构面向读者：目录 / 摘要 / 正文 / 附录清晰分层。
- 网页原型符合组件化、响应式与可访问性（a11y）原则。
- 最终回复说明产出了什么、预览链接在哪里、还剩什么需要用户决策。`,
  },
]

export const DEFAULT_CUSTOM_AGENT_TOOLS = AGENT_TOOL_PRESETS[0].tools

/**
 * Metadata for the 6 UI-selectable tools. Baseline tools have a separate
 * metadata record (`BASELINE_AGENT_TOOL_META`) used for the read-only
 * "baseline tools" hint section in the create/edit dialog.
 */
export const AGENT_TOOL_META: Record<AgentToolName, { label: string; desc: string }> = {
  write_artifact: { label: '创建产物', desc: '生成可预览的代码 / 网页 / 文档 / PPT，支持多版本迭代' },
  deploy_artifact: { label: '部署网页', desc: '把网页产物发布为本地静态站点，生成预览链接与下载包' },
  deploy_workspace: { label: '部署目录', desc: '把工作区内 dist/build/out 等静态目录生成预览链接与下载包' },
  read_artifact: { label: '读取产物', desc: '查看会话中已有产物的完整内容，便于在其基础上继续改' },
  web_search: { label: '联网搜索', desc: '用 Tavily 搜索公网获取实时信息；调用会消耗 Tavily 额度' },
  rag_search: { label: '知识库检索', desc: '在知识库中检索相关文档片段，返回匹配的文本块和来源信息' },
}

/**
 * Metadata for the 9 baseline tools, used to render the read-only hint
 * section in the create/edit agent dialog. These tools are always-on for
 * Custom adapter agents and cannot be toggled off.
 */
export const BASELINE_AGENT_TOOL_META: Record<
  (typeof BASELINE_AGENT_TOOLS)[number],
  { label: string; desc: string }
> = {
  read_attachment: { label: '读取附件', desc: '读取用户上传的文本 / 文件附件内容' },
  ask_user: { label: '结构化提问', desc: '让用户在明确选项中选择，用于范围、风格、平台等关键澄清' },
  fs_list: { label: '列出文件', desc: '列出工作区内的目录和文件，用于安全探索项目结构' },
  fs_read: { label: '读取文件', desc: '读取工作区内的文件（源码 / 配置等），仅限沙箱目录' },
  fs_write: { label: '写入文件', desc: '在工作区内新建 / 修改文件；review 模式下需用户批准' },
  fs_edit: { label: '编辑文件', desc: '精确替换文件中的唯一文本片段；review 模式下 diff 只高亮改的行' },
  fs_grep: { label: '搜索文本', desc: '用正则在 workspace 文件中搜索，返回结构化匹配结果；跳过二进制和依赖目录' },
  fs_glob: { label: '查找文件', desc: '用 glob 模式递归查找文件（如 **/*.tsx），返回路径和大小' },
  bash: { label: '执行命令', desc: '在工作区内运行命令行；受命令黑名单与沙箱目录约束' },
}

export interface AgentDraftAssumption {
  label: string
  detail: string
}

export interface AgentToolPermissionSummary {
  toolName: AgentToolName
  label: string
  desc: string
}

export interface AgentConfigDraft {
  name: string
  avatar: string
  description: string
  capabilities: string[]
  systemPrompt: string
  adapterName: AgentBuilderAdapter
  modelProvider?: AgentBuilderProvider
  modelId?: string
  toolNames: AgentToolName[]
  supportsVision: boolean
  /** custom adapter 启用的 MCP server ID 列表 */
  mcpServerIds: string[]
  rationale: string[]
  assumptions: AgentDraftAssumption[]
  toolPermissionSummaries: AgentToolPermissionSummary[]
}

export interface AgentDraftRequest {
  intent: string
  followUp?: string
}

export interface AgentDraftResponse {
  draft: AgentConfigDraft
}

/**
 * Filter persisted toolNames to only the 6 UI-selectable tools.
 * Baseline tools are not filtered here — they are merged at runtime by
 * the backend (`agent_runner.py`).
 */
export function normalizeAgentToolNames(toolNames: readonly string[]): AgentToolName[] {
  const allowed = new Set<string>(AVAILABLE_AGENT_TOOLS)
  const seen = new Set<string>()
  const normalized: AgentToolName[] = []

  for (const toolName of toolNames) {
    if (!allowed.has(toolName) || seen.has(toolName)) continue
    seen.add(toolName)
    normalized.push(toolName as AgentToolName)
  }

  return normalized
}

export function getAgentToolPreset(presetId: AgentToolPresetId): AgentToolPreset {
  return AGENT_TOOL_PRESETS.find((preset) => preset.id === presetId) ?? AGENT_TOOL_PRESETS[0]
}

export function buildToolPermissionSummaries(
  toolNames: readonly string[],
): AgentToolPermissionSummary[] {
  return normalizeAgentToolNames(toolNames).map((toolName) => ({
    toolName,
    ...AGENT_TOOL_META[toolName],
  }))
}

export function inferAgentToolPreset(intent: string, followUp?: string): AgentToolPresetId {
  const text = `${intent}\n${followUp ?? ''}`.toLowerCase()

  // Specific roles — checked before coder to avoid overlap
  // (e.g. "调研" should match researcher, not coder).
  if (/调研|联网搜索|搜索公网|market.?research|竞品|research|文献综述|行业分析/.test(text)) {
    return 'researcher'
  }
  if (/协调|派发|项目管理|拆分任务|orchestrat|coordinat|项目经理|群聊/.test(text)) {
    return 'orchestrator'
  }
  if (/文档|文案|报告|审查|评审|原型|网页|ppt|幻灯片|演示|tech.?writ|documentation|review|prototype|presentation|slides/.test(text)) {
    return 'writer'
  }

  // coder 关键词覆盖最广，放最后
  if (/代码|实现|开发|bug|重构|测试|前端|后端|源码|仓库|本地|文件|命令|终端|修复|调试|workspace|repo|code|implement|build|ship|cli|bash|test|lint|debug|refactor|frontend|backend/.test(text)) {
    return 'coder'
  }

  return 'coder'
}
