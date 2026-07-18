export type CodeIntelligenceStatus =
  | 'disabled'
  | 'preparing_runtime'
  | 'queued'
  | 'indexing'
  | 'ready'
  | 'syncing'
  | 'rebuilding'
  | 'cancelling'
  | 'stale'
  | 'failed'
  | 'interrupted'

export type CodeIntelligenceTone =
  | 'disabled'
  | 'building'
  | 'ready'
  | 'stale'
  | 'failed'
  | 'interrupted'

export interface CodeIntelligenceStatusVisual {
  tone: CodeIntelligenceTone
  label: string
  spinning: boolean
}

const BUILDING_STATUSES = new Set<CodeIntelligenceStatus>([
  'preparing_runtime',
  'queued',
  'indexing',
  'syncing',
  'rebuilding',
  'cancelling',
])

export function getCodeIntelligenceStatusVisual(
  status: CodeIntelligenceStatus,
): CodeIntelligenceStatusVisual {
  if (BUILDING_STATUSES.has(status)) {
    return { tone: 'building', label: '源码智能处理中', spinning: true }
  }
  const labels: Record<Exclude<CodeIntelligenceTone, 'building'>, string> = {
    disabled: '源码智能未启用',
    ready: '源码智能已就绪',
    stale: '源码索引需要同步',
    failed: '源码智能失败',
    interrupted: '源码智能已中断',
  }
  const tone = status as Exclude<CodeIntelligenceTone, 'building'>
  return { tone, label: labels[tone], spinning: false }
}
export interface CodeIntelligenceDetailSource {
  projectPath: string
  runtimeVersion: string | null
  phase: string | null
  counts: { files: number; symbols: number; relationships: number }
  lastSyncAt: number | null
  error: string | null
}

export interface CodeIntelligenceDetailRow {
  label: string
  value: string
}

export function buildCodeIntelligenceDetailRows(
  status: CodeIntelligenceDetailSource,
): CodeIntelligenceDetailRow[] {
  const rows: CodeIntelligenceDetailRow[] = [
    { label: '项目', value: status.projectPath },
  ]
  if (status.runtimeVersion) {
    rows.push({ label: '运行时', value: status.runtimeVersion })
  }
  if (Object.values(status.counts).some((count) => count > 0)) {
    rows.push({
      label: '统计',
      value: `${status.counts.files} 文件 · ${status.counts.symbols} 符号 · ${status.counts.relationships} 关系`,
    })
  }
  if (status.lastSyncAt) {
    rows.push({
      label: '最近同步',
      value: new Date(status.lastSyncAt).toLocaleString('zh-CN'),
    })
  }
  return rows
}

export type CodeIntelligencePanelTone = 'neutral' | 'working' | 'success' | 'warning' | 'error'

export function getCodeIntelligencePanelSummary(
  status: CodeIntelligenceStatus,
): { label: string; tone: CodeIntelligencePanelTone } {
  const summaries: Record<CodeIntelligenceStatus, { label: string; tone: CodeIntelligencePanelTone }> = {
    disabled: { label: '未启用', tone: 'neutral' },
    preparing_runtime: { label: '正在准备运行时', tone: 'working' },
    queued: { label: '等待建立索引', tone: 'working' },
    indexing: { label: '正在建立索引', tone: 'working' },
    ready: { label: '源码索引已就绪', tone: 'success' },
    syncing: { label: '正在同步索引', tone: 'working' },
    rebuilding: { label: '正在重建索引', tone: 'working' },
    cancelling: { label: '正在取消任务', tone: 'working' },
    stale: { label: '源码索引需要同步', tone: 'warning' },
    failed: { label: '源码智能任务失败', tone: 'error' },
    interrupted: { label: '源码智能任务已中断', tone: 'warning' },
  }
  return summaries[status]
}
export type CodeIntelligenceToggleAction = 'enable' | 'disable'

export interface CodeIntelligenceToggleResult {
  cancelled: boolean
  enabled: boolean
  error: string | null
}

export async function performCodeIntelligenceToggle(options: {
  enabled: boolean
  confirm: (message: string) => boolean
  run: (action: CodeIntelligenceToggleAction) => Promise<void>
  onPendingChange: (pending: boolean) => void
}): Promise<CodeIntelligenceToggleResult> {
  const action: CodeIntelligenceToggleAction = options.enabled ? 'disable' : 'enable'
  const message = options.enabled
    ? '确认停用源码智能？正在进行的任务会停止，已有 .codegraph 索引会保留。'
    : '确认启用源码智能？AChat 将准备托管运行时，并在真实项目中创建 .codegraph 索引。'
  if (!options.confirm(message)) {
    return { cancelled: true, enabled: options.enabled, error: null }
  }

  options.onPendingChange(true)
  try {
    await options.run(action)
    return { cancelled: false, enabled: !options.enabled, error: null }
  } catch (error) {
    return {
      cancelled: false,
      enabled: options.enabled,
      error: error instanceof Error ? error.message : String(error),
    }
  } finally {
    options.onPendingChange(false)
  }
}
export function isCodeIntelligenceSwitchOn(state: {
  enabled: boolean
  status: CodeIntelligenceStatus
}): boolean {
  return state.enabled
}
export type CodeIntelligencePanelAction = 'cancel' | 'retry' | 'sync' | 'rebuild'

export function getCodeIntelligenceActions(
  status: CodeIntelligenceStatus,
): CodeIntelligencePanelAction[] {
  if (BUILDING_STATUSES.has(status)) return ['cancel']
  if (status === 'ready' || status === 'stale') return ['sync', 'rebuild']
  if (status === 'failed' || status === 'interrupted') return ['retry']
  return []
}

export interface CodeIntelligenceNotice {
  tone: 'success' | 'error'
  message: string
}

export function getCodeIntelligenceTransitionNotice(
  previous: CodeIntelligenceStatus | null,
  current: CodeIntelligenceStatus,
): CodeIntelligenceNotice | null {
  if (previous === null || !BUILDING_STATUSES.has(previous)) return null
  if (current === 'ready') return { tone: 'success', message: '源码智能已就绪' }
  if (current === 'failed') return { tone: 'error', message: '源码智能任务失败' }
  return null
}

export function scheduleCodeIntelligenceNoticeDismiss(
  dismiss: () => void,
  delayMs = 4000,
): () => void {
  const timer = setTimeout(dismiss, delayMs)
  return () => clearTimeout(timer)
}

export function shouldPollCodeIntelligence(
  panelOpen: boolean,
  status: CodeIntelligenceStatus,
): boolean {
  return panelOpen || BUILDING_STATUSES.has(status)
}

export function startCodeIntelligencePolling(
  poll: () => void | Promise<void>,
  intervalMs = 1500,
): () => void {
  const timer = setInterval(() => void poll(), intervalMs)
  return () => clearInterval(timer)
}
export function getCodeIntelligenceProgress(
  status: CodeIntelligenceStatus,
  phase: string | null,
  progressPercent: number | null,
): { active: boolean; label: string; percent: number | null } {
  const visual = getCodeIntelligenceStatusVisual(status)
  if (!visual.spinning) {
    return { active: false, label: visual.label, percent: null }
  }
  return {
    active: true,
    label: '正在构建源码索引',
    percent: Math.max(0, Math.min(progressPercent ?? 0, 99)),
  }
}

export function getCodeIntelligenceSwitchVisual(checked: boolean): {
  track: string
  thumb: string
} {
  return {
    track: checked ? 'bg-primary' : 'bg-muted-foreground/30',
    thumb: [
      'absolute left-0.5 top-0.5 size-4 rounded-full bg-background shadow-sm transition-transform',
      checked ? 'translate-x-4' : 'translate-x-0',
    ].join(' '),
  }
}
