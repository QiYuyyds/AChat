import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildCodeIntelligenceDetailRows,
  getCodeIntelligenceStatusVisual,
  performCodeIntelligenceToggle,
  isCodeIntelligenceSwitchOn,
  getCodeIntelligenceActions,
  getCodeIntelligencePanelSummary,
  getCodeIntelligenceProgress,
  getCodeIntelligenceSwitchVisual,
  getCodeIntelligenceTransitionNotice,
  shouldPollCodeIntelligence,
  scheduleCodeIntelligenceNoticeDismiss,
  startCodeIntelligencePolling,
} from '@/lib/code-intelligence'

describe('getCodeIntelligenceStatusVisual', () => {
  it.each([
    ['disabled', 'disabled', false],
    ['indexing', 'building', true],
    ['ready', 'ready', false],
    ['stale', 'stale', false],
    ['failed', 'failed', false],
    ['interrupted', 'interrupted', false],
  ] as const)('maps %s to an accessible icon state', (status, tone, spinning) => {
    expect(getCodeIntelligenceStatusVisual(status)).toMatchObject({ tone, spinning })
  })
})
describe('buildCodeIntelligenceDetailRows', () => {
  it('shows only useful project, runtime, counts and sync details', () => {
    const rows = buildCodeIntelligenceDetailRows({
      projectPath: 'C:/repo',
      runtimeVersion: '0.9.3',
      phase: 'Parsing TypeScript',
      counts: { files: 12, symbols: 34, relationships: 56 },
      lastSyncAt: 1_700_000_000_000,
      error: 'index failed',
    })

    expect(rows.map((row) => row.label)).toEqual([
      '项目',
      '运行时',
      '统计',
      '最近同步',
    ])
    expect(rows[2]?.value).toBe('12 文件 · 34 符号 · 56 关系')
  })

  it('hides empty runtime, counts, sync and placeholder fields', () => {
    expect(buildCodeIntelligenceDetailRows({
      projectPath: 'C:/repo',
      runtimeVersion: null,
      phase: null,
      counts: { files: 0, symbols: 0, relationships: 0 },
      lastSyncAt: null,
      error: null,
    })).toEqual([{ label: '项目', value: 'C:/repo' }])
  })
})

describe('getCodeIntelligencePanelSummary', () => {
  it.each([
    ['disabled', '未启用', 'neutral'],
    ['preparing_runtime', '正在准备运行时', 'working'],
    ['queued', '等待建立索引', 'working'],
    ['indexing', '正在建立索引', 'working'],
    ['ready', '源码索引已就绪', 'success'],
    ['failed', '源码智能任务失败', 'error'],
    ['interrupted', '源码智能任务已中断', 'warning'],
  ] as const)('maps %s to an explicit panel summary', (status, label, tone) => {
    expect(getCodeIntelligencePanelSummary(status)).toEqual({ label, tone })
  })
})
describe('performCodeIntelligenceToggle', () => {
  it('confirms enable, calls the action and reports pending state', async () => {
    const pending: boolean[] = []
    const actions: string[] = []
    const result = await performCodeIntelligenceToggle({
      enabled: false,
      confirm: (message) => message.includes('.codegraph'),
      run: async (action) => { actions.push(action) },
      onPendingChange: (value) => pending.push(value),
    })

    expect(actions).toEqual(['enable'])
    expect(pending).toEqual([true, false])
    expect(result).toEqual({ cancelled: false, enabled: true, error: null })
  })

  it('rolls back enabled intent when disable fails', async () => {
    const result = await performCodeIntelligenceToggle({
      enabled: true,
      confirm: () => true,
      run: async () => { throw new Error('network failed') },
      onPendingChange: () => undefined,
    })

    expect(result).toEqual({ cancelled: false, enabled: true, error: 'network failed' })
  })

  it('does not enter pending state when confirmation is cancelled', async () => {
    const pending: boolean[] = []
    const result = await performCodeIntelligenceToggle({
      enabled: false,
      confirm: () => false,
      run: async () => { throw new Error('must not run') },
      onPendingChange: (value) => pending.push(value),
    })

    expect(pending).toEqual([])
    expect(result.cancelled).toBe(true)
  })
})
describe('isCodeIntelligenceSwitchOn', () => {
  it.each(['preparing_runtime', 'indexing', 'ready', 'failed', 'interrupted'] as const)(
    'keeps the switch on for enabled intent in %s',
    (status) => {
      expect(isCodeIntelligenceSwitchOn({ enabled: true, status })).toBe(true)
    },
  )

  it('shows off only when enabled intent is false', () => {
    expect(isCodeIntelligenceSwitchOn({ enabled: false, status: 'disabled' })).toBe(false)
  })
})
describe('getCodeIntelligenceActions', () => {
  it.each([
    ['disabled', []],
    ['indexing', ['cancel']],
    ['ready', ['sync', 'rebuild']],
    ['failed', ['retry']],
    ['interrupted', ['retry']],
  ] as const)('returns valid actions for %s', (status, actions) => {
    expect(getCodeIntelligenceActions(status)).toEqual(actions)
  })
})

describe('getCodeIntelligenceTransitionNotice', () => {
  it('reports exactly terminal completion and failure transitions', () => {
    expect(getCodeIntelligenceTransitionNotice('indexing', 'ready')).toMatchObject({
      tone: 'success',
    })
    expect(getCodeIntelligenceTransitionNotice('indexing', 'failed')).toMatchObject({
      tone: 'error',
    })
    expect(getCodeIntelligenceTransitionNotice(null, 'ready')).toBeNull()
    expect(getCodeIntelligenceTransitionNotice('ready', 'syncing')).toBeNull()
  })
})
describe('source intelligence polling', () => {
  afterEach(() => vi.useRealTimers())

  it('polls only while the panel is open or work is non-terminal', () => {
    expect(shouldPollCodeIntelligence(false, 'ready')).toBe(false)
    expect(shouldPollCodeIntelligence(false, 'failed')).toBe(false)
    expect(shouldPollCodeIntelligence(false, 'indexing')).toBe(true)
    expect(shouldPollCodeIntelligence(true, 'ready')).toBe(true)
  })

  it('stops the interval when cleanup runs', () => {
    vi.useFakeTimers()
    const poll = vi.fn()
    const cleanup = startCodeIntelligencePolling(poll, 1000)

    vi.advanceTimersByTime(2000)
    expect(poll).toHaveBeenCalledTimes(2)
    cleanup()
    vi.advanceTimersByTime(2000)
    expect(poll).toHaveBeenCalledTimes(2)
  })
})
describe('getCodeIntelligenceProgress', () => {
  it('shows the exact server percentage only for active work', () => {
    expect(getCodeIntelligenceProgress('indexing', '解析 TypeScript', 46)).toEqual({
      active: true,
      label: '正在构建源码索引',
      percent: 46,
    })
    expect(getCodeIntelligenceProgress('ready', null, null)).toEqual({
      active: false,
      label: '源码智能已就绪',
      percent: null,
    })
  })
})

describe('getCodeIntelligenceSwitchVisual', () => {
  it('keeps the thumb explicitly inset inside the track in both states', () => {
    const off = getCodeIntelligenceSwitchVisual(false)
    const on = getCodeIntelligenceSwitchVisual(true)

    expect(off.thumb).toContain('left-0.5')
    expect(on.thumb).toContain('left-0.5')
    expect(off.thumb).toContain('translate-x-0')
    expect(on.thumb).toContain('translate-x-4')
  })
})

describe('scheduleCodeIntelligenceNoticeDismiss', () => {
  afterEach(() => vi.useRealTimers())

  it('dismisses a notice after four seconds', () => {
    vi.useFakeTimers()
    const dismiss = vi.fn()
    scheduleCodeIntelligenceNoticeDismiss(dismiss)

    vi.advanceTimersByTime(3999)
    expect(dismiss).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(dismiss).toHaveBeenCalledTimes(1)
  })

  it('cancels an old timer and gives a replacement notice a fresh lifetime', () => {
    vi.useFakeTimers()
    const firstDismiss = vi.fn()
    const cancelFirst = scheduleCodeIntelligenceNoticeDismiss(firstDismiss)
    vi.advanceTimersByTime(2000)
    cancelFirst()

    const replacementDismiss = vi.fn()
    scheduleCodeIntelligenceNoticeDismiss(replacementDismiss)
    vi.advanceTimersByTime(3999)
    expect(firstDismiss).not.toHaveBeenCalled()
    expect(replacementDismiss).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(replacementDismiss).toHaveBeenCalledTimes(1)
  })
})
