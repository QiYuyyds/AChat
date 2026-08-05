/**
 * Task board shared constants — used by both frontend components and stores.
 */

export const TASK_STATUSES = [
  'backlog',
  'todo',
  'in_progress',
  'in_review',
  'done',
  'blocked',
  'canceled',
] as const

export const TASK_PRIORITIES = [
  'none',
  'urgent',
  'high',
  'medium',
  'low',
] as const

export const TASK_STATUS_LABELS: Record<string, string> = {
  backlog: '待办池',
  todo: '待办',
  in_progress: '进行中',
  in_review: '评审中',
  done: '已完成',
  blocked: '已阻塞',
  canceled: '已取消',
}

export const TASK_PRIORITY_LABELS: Record<string, string> = {
  none: '无',
  urgent: '紧急',
  high: '高',
  medium: '中',
  low: '低',
}

/** 优先级顶部色条（卡片顶部全宽）— 使用 OKLCH 降饱和色系，和项目色系一致 */
export const TASK_PRIORITY_BAR_COLORS: Record<string, string> = {
  none: 'bg-transparent',
  urgent: 'bg-[oklch(0.52_0.16_25)]',
  high: 'bg-[oklch(0.70_0.11_70)]',
  medium: 'bg-[oklch(0.75_0.10_85)]',
  low: 'bg-[oklch(0.60_0.08_257)]',
}

/** 优先级圆点（卡片底部信息栏 + 详情面板） */
export const TASK_PRIORITY_DOT_COLORS: Record<string, string> = {
  none: 'bg-zinc-400',
  urgent: 'bg-[oklch(0.52_0.16_25)]',
  high: 'bg-[oklch(0.70_0.11_70)]',
  medium: 'bg-[oklch(0.75_0.10_85)]',
  low: 'bg-[oklch(0.60_0.08_257)]',
}

/** 优先级文字颜色（详情面板 badge） */
export const TASK_PRIORITY_TEXT_COLORS: Record<string, string> = {
  none: 'text-muted-foreground',
  urgent: 'text-[oklch(0.52_0.16_25)]',
  high: 'text-[oklch(0.70_0.11_70)]',
  medium: 'text-[oklch(0.65_0.10_85)]',
  low: 'text-[oklch(0.60_0.08_257)]',
}

/** 优先级背景色（详情面板 badge 底色） */
export const TASK_PRIORITY_BADGE_BG: Record<string, string> = {
  none: 'bg-muted',
  urgent: 'bg-[oklch(0.52_0.16_25_/_0.1)]',
  high: 'bg-[oklch(0.70_0.11_70_/_0.1)]',
  medium: 'bg-[oklch(0.75_0.10_85_/_0.12)]',
  low: 'bg-[oklch(0.60_0.08_257_/_0.1)]',
}

/**
 * @deprecated 使用 TASK_PRIORITY_BAR_COLORS / TASK_PRIORITY_DOT_COLORS 代替。
 * 保留用于 task-board-detail.tsx 等旧引用的过渡兼容。
 */
export const TASK_PRIORITY_COLORS: Record<string, string> = TASK_PRIORITY_BAR_COLORS

export interface TaskColumnAccent {
  dot: string
  bar: string
  glow: string
  headerBg: string
  icon: string
}

export const TASK_COLUMN_ACCENTS: Record<string, TaskColumnAccent> = {
  backlog: {
    dot: 'bg-zinc-400',
    bar: 'bg-zinc-300 dark:bg-zinc-600',
    glow: 'bg-zinc-500/5',
    headerBg: 'bg-zinc-500/[0.03]',
    icon: 'text-zinc-400',
  },
  todo: {
    dot: 'bg-blue-400',
    bar: 'bg-blue-300 dark:bg-blue-600',
    glow: 'bg-blue-500/5',
    headerBg: 'bg-blue-500/[0.03]',
    icon: 'text-blue-400',
  },
  in_progress: {
    dot: 'bg-amber-400',
    bar: 'bg-amber-300 dark:bg-amber-600',
    glow: 'bg-amber-500/5',
    headerBg: 'bg-amber-500/[0.04]',
    icon: 'text-amber-400',
  },
  in_review: {
    dot: 'bg-violet-400',
    bar: 'bg-violet-300 dark:bg-violet-600',
    glow: 'bg-violet-500/5',
    headerBg: 'bg-violet-500/[0.03]',
    icon: 'text-violet-400',
  },
  done: {
    dot: 'bg-emerald-400',
    bar: 'bg-emerald-300 dark:bg-emerald-600',
    glow: 'bg-emerald-500/5',
    headerBg: 'bg-emerald-500/[0.03]',
    icon: 'text-emerald-400',
  },
  blocked: {
    dot: 'bg-red-400',
    bar: 'bg-red-300 dark:bg-red-600',
    glow: 'bg-red-500/5',
    headerBg: 'bg-red-500/[0.03]',
    icon: 'text-red-400',
  },
}

export const TASK_BOARD_COLUMNS: { status: string; label: string }[] = [
  { status: 'backlog', label: '待办池' },
  { status: 'todo', label: '待办' },
  { status: 'in_progress', label: '进行中' },
  { status: 'in_review', label: '评审中' },
  { status: 'done', label: '已完成' },
  { status: 'blocked', label: '已阻塞' },
]
