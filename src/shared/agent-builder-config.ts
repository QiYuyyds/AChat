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
  'fs_list',
  'fs_read',
  'fs_write',
  'bash',
  'web_search',
] as const

export type AgentToolName = (typeof AVAILABLE_AGENT_TOOLS)[number]
export type AgentToolPresetId = 'all-purpose' | 'local-code' | 'artifact' | 'review'

export interface AgentToolPreset {
  id: AgentToolPresetId
  label: string
  desc: string
  tools: readonly AgentToolName[]
}

export const AGENT_TOOL_PRESETS: readonly AgentToolPreset[] = [
  {
    id: 'all-purpose',
    label: '全栈通用',
    desc: '本地代码 + artifact 交付',
    // web_search intentionally excluded — keep it opt-in (avoids spending Tavily
    // credits on every all-purpose agent). Tick it explicitly when search is needed.
    tools: [
      'write_artifact',
      'deploy_artifact',
      'deploy_workspace',
      'read_artifact',
      'read_attachment',
      'ask_user',
      'fs_list',
      'fs_read',
      'fs_write',
      'bash',
    ],
  },
  {
    id: 'local-code',
    label: '本地代码',
    desc: '读写 workspace 并运行命令',
    tools: ['deploy_workspace', 'read_artifact', 'read_attachment', 'ask_user', 'fs_list', 'fs_read', 'fs_write', 'bash'],
  },
  {
    id: 'artifact',
    label: '产物交付',
    desc: '网页、文档、原型卡片',
    tools: ['write_artifact', 'deploy_artifact', 'deploy_workspace', 'read_artifact', 'read_attachment', 'ask_user'],
  },
  {
    id: 'review',
    label: '审查验证',
    desc: '读取产物/文件并跑检查',
    tools: ['read_artifact', 'read_attachment', 'ask_user', 'fs_list', 'fs_read', 'bash'],
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
  fs_list: { label: '列出文件', desc: '列出工作区内的目录和文件，用于安全探索项目结构' },
  fs_read: { label: '读取文件', desc: '读取工作区内的文件（源码 / 配置等），仅限沙箱目录' },
  fs_write: { label: '写入文件', desc: '在工作区内新建 / 修改文件；review 模式下需用户批准' },
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
