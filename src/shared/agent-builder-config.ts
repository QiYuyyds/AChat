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

export const AVAILABLE_AGENT_TOOLS = [
  'write_artifact',
  'deploy_artifact',
  'deploy_workspace',
  'read_artifact',
  'read_attachment',
  'ask_user',
  'plan_tasks',
  'fs_list',
  'fs_read',
  'fs_write',
  'fs_edit',
  'fs_grep',
  'fs_glob',
  'bash',
  'web_search',
] as const

export type AgentToolName = (typeof AVAILABLE_AGENT_TOOLS)[number]
export type AgentToolPresetId =
  | 'all-purpose'
  | 'local-code'
  | 'artifact'
  | 'review'
  | 'tech-writing'
  | 'testing-qa'
  | 'frontend-design'
  | 'researcher'
  | 'data-analysis'

export interface AgentToolPreset {
  id: AgentToolPresetId
  label: string
  desc: string
  tools: readonly AgentToolName[]
  systemPromptTemplate: string
}

export const AGENT_TOOL_PRESETS: readonly AgentToolPreset[] = [
  {
    id: 'all-purpose',
    label: '全栈通用',
    desc: '本地代码 + artifact 交付',
    // plan_tasks (Orchestrator-only) and web_search (opt-in, consumes Tavily
    // credits) are both excluded from the all-purpose preset — tick them
    // explicitly when needed.
    tools: AVAILABLE_AGENT_TOOLS.filter((t) => t !== 'plan_tasks' && t !== 'web_search'),
    systemPromptTemplate: `你是一个 AChat custom agent。你的任务是理解用户目标，使用已启用的工具完成工作，并把结果清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；只有在用户提到附件、已有产物或工作区文件时，才调用对应读取工具。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 产出代码、网页、文档或设计稿时，优先用 write_artifact 创建结构化产物；网页产物完成后再调用 deploy_artifact。
5. 探索项目目录时优先用 fs_list，再用 fs_read 读取具体文件；使用 fs_write 或 bash 前确认确有必要，并只在当前 workspace 范围内操作。
6. 最终回复保持简洁，说明完成了什么、产物在哪里、还剩什么需要用户决策。`,
  },
  {
    id: 'local-code',
    label: '本地代码',
    desc: '读写 workspace 并运行命令',
    tools: ['deploy_workspace', 'read_artifact', 'read_attachment', 'ask_user', 'fs_list', 'fs_read', 'fs_write', 'fs_edit', 'fs_grep', 'fs_glob', 'bash'],
    systemPromptTemplate: `你是一名本地代码开发与调试工程师。你的任务是理解用户在当前 workspace 的代码目标，使用已启用的工具直接修改源码、运行命令，并把可验证的结果交付给用户。

工作原则：
1. 先判断需要什么上下文；用 fs_list 探索项目结构，用 fs_read 读取相关源码，用 fs_grep 搜索符号与引用，用 fs_glob 定位文件；用户提到附件或已有产物时才调用对应读取工具。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 修改源码时优先用 fs_edit 做精确局部替换（old_string 必须唯一），大段新建或全量重写才用 fs_write；不要用 write_artifact 代替源码落盘。
5. 改动前先读目标文件确认当前内容；执行 bash 命令前确认确有必要且只在当前 workspace 范围内操作；改完用 bash 跑测试或构建验证。
6. 最终回复保持简洁，说明改了哪些文件、命令结果如何、还剩什么需要用户决策。`,
  },
  {
    id: 'artifact',
    label: '产物交付',
    desc: '网页、文档、原型卡片',
    tools: ['write_artifact', 'deploy_artifact', 'deploy_workspace', 'read_artifact', 'read_attachment', 'ask_user'],
    systemPromptTemplate: `你是一名产物交付工程师。你的任务是理解用户想交付的产物目标，使用已启用的工具创建可预览的网页、文档或原型，并把结构化产物清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 read_artifact 查看已有产物以便在其基础上迭代，用户提到附件时用 read_attachment；本角色一般不直接读 workspace 源码。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 产出网页、文档、原型或设计稿时，优先用 write_artifact 创建结构化产物；网页产物完成后再调用 deploy_artifact 生成预览链接；支持多版本迭代。
5. 本角色不直接修改 workspace 源码文件；如需读取工作区静态目录可用 deploy_workspace 生成预览。
6. 最终回复保持简洁，说明产出了什么、预览链接在哪里、还剩什么需要用户决策。`,
  },
  {
    id: 'review',
    label: '审查验证',
    desc: '读取产物/文件并跑检查',
    tools: ['read_artifact', 'read_attachment', 'ask_user', 'fs_list', 'fs_read', 'bash'],
    systemPromptTemplate: `你是一名代码与产物审查员。你的任务是理解审查范围，使用已启用的只读工具检查代码或产物，并把发现的风险与建议清晰交付给用户。

工作原则：
1. 先判断需要审查什么；用 read_artifact 查看产物，用 fs_list/fs_read 查看源码，用 fs_grep 搜索可疑模式；用户给附件时用 read_attachment。
2. 多步骤审查先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 审查结论优先用 write_artifact 产出结构化报告；本角色不创建业务代码或产物，只产出审查意见。
5. 本角色只读不写：不使用 fs_write/fs_edit 修改任何文件；bash 仅用于运行只读检查命令（lint/typecheck/test），不得有副作用。
6. 最终回复保持简洁，说明发现了什么风险、严重程度、建议如何处理。`,
  },
  {
    id: 'tech-writing',
    label: '技术写作',
    desc: '采集源码信息产出文档',
    tools: ['write_artifact', 'read_artifact', 'read_attachment', 'ask_user', 'fs_read', 'fs_list', 'fs_glob', 'fs_grep'],
    systemPromptTemplate: `你是一名技术文档工程师。你的任务是理解用户想交付的文档目标，使用已启用的工具采集准确信息，并把结构化文档清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用户提到源码、API 或已有产物时，用 fs_list/fs_glob 定位文件，fs_read 读取实现，fs_grep 搜索特定符号或注释；用户给附件时用 read_attachment。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 产出文档时优先用 write_artifact 创建结构化产物；面向读者组织结构，所有 API、路径、行为描述必须来自源码实测，不得臆造。
5. 引用源码时写明文件路径与行号范围；探索项目目录时优先用 fs_list，再用 fs_read 读取具体文件；本角色不修改源码。
6. 最终回复保持简洁，说明文档覆盖了什么、产物在哪里、还剩什么需要用户确认。`,
  },
  {
    id: 'testing-qa',
    label: '测试 QA',
    desc: '编写测试并运行验证',
    tools: ['bash', 'fs_read', 'fs_list', 'fs_glob', 'fs_grep', 'fs_write', 'read_artifact', 'ask_user', 'write_artifact'],
    systemPromptTemplate: `你是一名测试工程师。你的任务是理解待测目标，使用已启用的工具编写测试、运行验证、定位回归，并把测试结果与覆盖情况清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 fs_grep 搜索现有测试覆盖与断言，用 fs_read 读取待测实现，用 fs_list/fs_glob 定位测试目录；用户提到已有产物时用 read_artifact。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 编写测试用例用 fs_write 创建测试文件，测试报告用 write_artifact 产出结构化产物；优先覆盖边界、异常与回归路径。
5. 用 bash 运行测试/lint 命令验证；fs_write 仅限创建测试文件，不修改业务源码；所有操作只在当前 workspace 范围内。
6. 最终回复保持简洁，说明覆盖了什么、哪些用例失败、建议如何修复。`,
  },
  {
    id: 'frontend-design',
    label: '前端/设计',
    desc: 'UI 产物与前端源码',
    tools: ['write_artifact', 'deploy_artifact', 'read_artifact', 'ask_user', 'fs_read', 'fs_list', 'fs_glob', 'fs_grep', 'fs_write', 'fs_edit'],
    systemPromptTemplate: `你是一名前端工程师与设计师。你的任务是理解用户的前端交付目标，使用已启用的工具创建 UI 产物、修改前端源码，并把可预览的结果清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 fs_list/fs_glob 定位组件与样式文件，用 fs_read 读取现有实现，用 fs_grep 搜索样式或组件引用；用户提到已有产物时用 read_artifact。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 创建可预览的网页/原型用 write_artifact，完成后调用 deploy_artifact 生成预览；修改前端源码用 fs_edit 做精确替换或 fs_write 新建组件。
5. 改动前先读目标文件确认当前内容；遵循组件化、响应式与可访问性（a11y）原则；所有操作只在当前 workspace 范围内。
6. 最终回复保持简洁，说明改了哪些文件或产出了什么、预览链接在哪里、还剩什么需要用户决策。`,
  },
  {
    id: 'researcher',
    label: '调研员',
    desc: '联网搜索与交叉验证',
    tools: ['web_search', 'ask_user', 'read_attachment', 'write_artifact', 'read_artifact'],
    systemPromptTemplate: `你是一名调研分析师。你的任务是理解用户的调研目标，使用已启用的工具联网搜索、交叉验证，并把结构化调研报告清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 web_search 搜索公网获取实时信息，用户给参考资料时用 read_attachment；本角色不直接读 workspace 源码。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 调研结论优先用 write_artifact 产出结构化报告；多源交叉验证，标注来源与时效性，区分事实与推测。
5. 本角色不使用 fs_*/bash 等本地代码工具；所有信息来自 web_search 与用户提供的附件。
6. 最终回复保持简洁，说明调研了什么、关键结论、信息来源与时效、还剩什么需要用户确认。`,
  },
  {
    id: 'data-analysis',
    label: '数据分析',
    desc: '清洗数据与生成图表',
    tools: ['bash', 'fs_read', 'fs_write', 'fs_list', 'fs_glob', 'read_attachment', 'write_artifact', 'ask_user'],
    systemPromptTemplate: `你是一名数据分析师。你的任务是理解用户的数据分析目标，使用已启用的工具清洗数据、运行处理脚本、生成图表，并把分析结论清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 read_attachment 读取用户上传的 csv/json 数据，用 fs_list/fs_glob 定位工作区数据文件，用 fs_read 读取已有脚本。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 数据清洗与处理脚本用 fs_write 创建，处理结果与图表用 write_artifact 产出结构化产物；所有结论必须基于实际数据，不得臆造。
5. 用 bash 运行处理脚本验证结果；数据清洗优先于分析；标注样本量与局限性；所有操作只在当前 workspace 范围内。
6. 最终回复保持简洁，说明分析了什么、关键结论、数据来源与局限、还剩什么需要用户决策。`,
  },
]

export const DEFAULT_CUSTOM_AGENT_TOOLS = AGENT_TOOL_PRESETS[0].tools

export const AGENT_TOOL_META: Record<AgentToolName, { label: string; desc: string }> = {
  write_artifact: { label: '创建产物', desc: '生成可预览的代码 / 网页 / 文档 / PPT，支持多版本迭代' },
  deploy_artifact: { label: '部署网页', desc: '把网页产物发布为本地静态站点，生成预览链接与下载包' },
  deploy_workspace: { label: '部署目录', desc: '把工作区内 dist/build/out 等静态目录生成预览链接与下载包' },
  read_artifact: { label: '读取产物', desc: '查看会话中已有产物的完整内容，便于在其基础上继续改' },
  read_attachment: { label: '读取附件', desc: '读取用户上传的文本 / 文件附件内容' },
  ask_user: { label: '结构化提问', desc: '让用户在明确选项中选择，用于范围、风格、平台等关键澄清' },
  plan_tasks: { label: '任务规划', desc: 'Orchestrator 专用：拆解用户目标为子任务并分派给其他 Agent' },
  fs_list: { label: '列出文件', desc: '列出工作区内的目录和文件，用于安全探索项目结构' },
  fs_read: { label: '读取文件', desc: '读取工作区内的文件（源码 / 配置等），仅限沙箱目录' },
  fs_write: { label: '写入文件', desc: '在工作区内新建 / 修改文件；review 模式下需用户批准' },
  fs_edit: { label: '编辑文件', desc: '精确替换文件中的唯一文本片段；review 模式下 diff 只高亮改的行' },
  fs_grep: { label: '搜索文本', desc: '用正则在 workspace 文件中搜索，返回结构化匹配结果；跳过二进制和依赖目录' },
  fs_glob: { label: '查找文件', desc: '用 glob 模式递归查找文件（如 **/*.tsx），返回路径和大小' },
  bash: { label: '执行命令', desc: '在工作区内运行命令行；受命令黑名单与沙箱目录约束' },
  web_search: { label: '联网搜索', desc: '用 Tavily 搜索公网获取实时信息；调用会消耗 Tavily 额度' },
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
  const wantsToWrite =
    /写|实现|开发|生成|创建|搭建|部署|build|implement|create|write|ship/.test(text) ||
    /修改(?!建议)/.test(text)
  const wantsReview = /审查|评审|检查|验证|验收|风险|review|audit|inspect|validate|verify/.test(text)
  if (wantsReview && !wantsToWrite) return 'review'

  // Specific roles — checked before general roles to avoid overlap
  // (e.g. "测试" should match testing-qa, not local-code).
  if (/调研|联网搜索|搜索公网|market.?research|竞品|research|文献综述/.test(text)) {
    return 'researcher'
  }
  if (/数据分析|数据清洗|数据可视化|统计|csv|excel|data.?analy|数据处理/.test(text)) {
    return 'data-analysis'
  }
  if (/技术文档|api文档|写文档|tech.?writ|documentation|文档工程师/.test(text)) {
    return 'tech-writing'
  }
  if (/测试|qa|用例|断言|test|回归|覆盖率/.test(text)) {
    return 'testing-qa'
  }
  if (/前端|ui设计|界面|样式|css|react|vue|组件|frontend|web.?design|交互设计/.test(text)) {
    return 'frontend-design'
  }

  if (
    /代码|源码|仓库|本地|文件|命令|终端|测试|修复|重构|调试|workspace|repo|repository|code|cli|bash|test|lint|debug|refactor/.test(
      text,
    )
  ) {
    return 'local-code'
  }

  if (
    /产物|网页|页面|原型|文档|报告|幻灯片|演示|图示|图表|设计稿|ppt|slides|presentation|website|document|diagram|mermaid|prototype/.test(
      text,
    )
  ) {
    return 'artifact'
  }

  return 'all-purpose'
}
