const ACHAT_TOOL_LABELS = {
  write_artifact: '创建产物',
  read_artifact: '读取产物',
  deploy_artifact: '部署网页',
  deploy_workspace: '部署目录',
  read_attachment: '读取附件',
  plan_tasks: '拆分任务',
  report_task_result: '上报结果',
  fs_read: '读取文件',
  fs_write: '写入文件',
  bash: '执行命令',
  ask_user: '询问用户',
  web_search: '联网搜索',
} as const

const EXTERNAL_TOOL_LABELS: Record<string, string> = {
  bash: '执行命令',
  read: '读取文件',
  write: '写入文件',
  edit: '编辑文件',
  multiedit: '批量编辑文件',
  glob: '查找文件',
  grep: '搜索文本',
  ls: '列出目录',
  todowrite: '更新任务',
  webfetch: '读取网页',
  websearch: '搜索网页',
}

const ACHAT_TOOL_NAMES = Object.keys(ACHAT_TOOL_LABELS).sort(
  (a, b) => b.length - a.length,
)

export function getToolDisplayName(toolName: string): string {
  const normalized = toolName.trim()
  const lower = normalized.toLowerCase()
  const achatName = findAChatToolName(lower)

  if (achatName) return ACHAT_TOOL_LABELS[achatName]
  return EXTERNAL_TOOL_LABELS[lower] ?? normalized
}

export function isBashToolName(toolName: string): boolean {
  const lower = toolName.trim().toLowerCase()
  return findAChatToolName(lower) === 'bash' || lower === 'bash'
}

function findAChatToolName(toolName: string): keyof typeof ACHAT_TOOL_LABELS | null {
  if (toolName in ACHAT_TOOL_LABELS) {
    return toolName as keyof typeof ACHAT_TOOL_LABELS
  }

  for (const name of ACHAT_TOOL_NAMES) {
    if (
      toolName.endsWith(`__${name}`) ||
      toolName.endsWith(`_${name}`) ||
      toolName.endsWith(`.${name}`)
    ) {
      return name as keyof typeof ACHAT_TOOL_LABELS
    }
  }

  return null
}
