// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'

// Mock stores before importing StreamProvider
const mockApplyEvent = vi.fn()
const mockSetStreamConnected = vi.fn()
const mockReconcile = vi.fn()
let mockWatermark = 0

vi.mock('@/stores/app-store', () => ({
  useAppStore: Object.assign(
    vi.fn((selector: (s: unknown) => unknown) => {
      if (typeof selector === 'function') {
        return selector({
          applyEvent: mockApplyEvent,
          setStreamConnected: mockSetStreamConnected,
        })
      }
      return mockApplyEvent
    }),
    {
      // StreamProvider reads connection-level state via getState() on reconnect.
      getState: () => ({
        streamSeqWatermark: mockWatermark,
        reconcileActiveConversation: mockReconcile,
      }),
    },
  ),
}))

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: vi.fn((selector) => {
    if (typeof selector === 'function') {
      return selector({ isAuthenticated: true })
    }
    return true
  }),
  getAccessToken: vi.fn(() => 'mock-token'),
}))

vi.mock('@/lib/config', () => ({
  API_BASE_URL: 'http://localhost:8000',
}))

// Import after mocks are set up
import { StreamProvider } from '@/components/stream-provider'

// ─── Mock EventSource ───────────────────────────────────────────────────────

class MockEventSource {
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((e: { data: string; lastEventId: string }) => void) | null = null
  url: string
  closed = false

  constructor(url: string) {
    this.url = url
    mockEventSourceInstances.push(this)
  }

  close() {
    this.closed = true
  }

  // Helper to simulate SSE message (id mirrors the frame's SSE `id:` field)
  emit(data: unknown, lastEventId = '') {
    this.onmessage?.({ data: JSON.stringify(data), lastEventId })
  }

  open() {
    this.onopen?.()
  }

  error() {
    this.onerror?.()
  }
}

let mockEventSourceInstances: MockEventSource[] = []

// ─── rAF mock ───────────────────────────────────────────────────────────────
// jsdom doesn't provide requestAnimationFrame; we control it manually.

let rafCallbacks: (() => void)[] = []

beforeEach(() => {
  mockEventSourceInstances = []
  rafCallbacks = []
  mockApplyEvent.mockClear()
  mockSetStreamConnected.mockClear()
  mockReconcile.mockClear()
  mockWatermark = 0

  // Provide rAF mocks on window
  ;(window as unknown as Record<string, unknown>).requestAnimationFrame = vi.fn(
    (cb: () => void) => {
      rafCallbacks.push(cb)
      return rafCallbacks.length // raf id
    },
  )
  ;(window as unknown as Record<string, unknown>).cancelAnimationFrame = vi.fn(
    (id: number) => {
      // No-op for simplicity; flushNow handles the cleanup
    },
  )

  // Provide EventSource on window
  ;(window as unknown as Record<string, unknown>).EventSource = MockEventSource
  ;(globalThis as unknown as Record<string, unknown>).EventSource = MockEventSource
})

afterEach(() => {
  cleanup()
  // Reset module-level state by re-importing is overkill; the StreamProvider
  // useEffect cleanup sets activeSource=null and refCount=0 when refCount<=0.
  // cleanup() unmounts → triggers useEffect cleanup → resets module state.
})

function flushRaf() {
  const callbacks = [...rafCallbacks]
  rafCallbacks = []
  for (const cb of callbacks) {
    cb()
  }
}

describe('StreamProvider rAF batching', () => {
  it('batches multiple events into a single rAF flush', () => {
    render(<StreamProvider>test</StreamProvider>)

    const source = mockEventSourceInstances[0]
    expect(source).toBeDefined()

    // Emit 5 events rapidly (before rAF fires)
    for (let i = 0; i < 5; i++) {
      source.emit({ type: 'part.delta', messageId: 'm1', partIndex: 0, delta: { type: 'text.append', text: `chunk${i}` } })
    }

    // Before rAF flush: applyEvent should NOT have been called
    // (connected event sets streamConnected but doesn't call applyEvent)
    expect(mockApplyEvent).not.toHaveBeenCalled()

    // Flush rAF — all 5 events should be applied in one flush
    flushRaf()

    expect(mockApplyEvent).toHaveBeenCalledTimes(5)
    // Verify events are applied in arrival order
    for (let i = 0; i < 5; i++) {
      const call = mockApplyEvent.mock.calls[i][0]
      expect(call.delta.text).toBe(`chunk${i}`)
    }
  })

  it('forwards each frame id as the dedup seq to applyEvent', () => {
    render(<StreamProvider>test</StreamProvider>)

    const source = mockEventSourceInstances[0]
    source.emit({ type: 'message.start', messageId: 'm1', agentId: 'a', runId: 'r' }, '7')
    source.emit({ type: 'message.end', messageId: 'm1' }, '8')
    // 合成帧没有 id（stream.resync / 旧后端）→ seq undefined，不去重
    source.emit({ type: 'stream.resync', timestamp: 1 })

    flushRaf()

    expect(mockApplyEvent.mock.calls[0]).toEqual([
      expect.objectContaining({ type: 'message.start' }),
      7,
    ])
    expect(mockApplyEvent.mock.calls[1]).toEqual([
      expect.objectContaining({ type: 'message.end' }),
      8,
    ])
    expect(mockApplyEvent.mock.calls[2]).toEqual([
      expect.objectContaining({ type: 'stream.resync' }),
      undefined,
    ])
  })

  it('applies heartbeat immediately without rAF queue', () => {
    render(<StreamProvider>test</StreamProvider>)

    const source = mockEventSourceInstances[0]

    // Emit a heartbeat event
    source.emit({ type: 'heartbeat' })

    // Heartbeat should immediately set streamConnected (not queued)
    expect(mockSetStreamConnected).toHaveBeenCalledWith(true)
    // No rAF should be scheduled for heartbeat
    expect(rafCallbacks.length).toBe(0)
    // applyEvent should not be called for heartbeat
    expect(mockApplyEvent).not.toHaveBeenCalled()
  })

  it('applies connected event immediately without rAF queue', () => {
    render(<StreamProvider>test</StreamProvider>)

    const source = mockEventSourceInstances[0]
    mockSetStreamConnected.mockClear()

    // Emit a connected event
    source.emit({ type: 'connected' })

    // Connected should immediately set streamConnected (not queued)
    expect(mockSetStreamConnected).toHaveBeenCalledWith(true)
    expect(rafCallbacks.length).toBe(0)
  })
})

describe('StreamProvider reconnect reconciliation fallback', () => {
  it('triggers reconciliation when no replay arrives within the grace window', () => {
    vi.useFakeTimers()
    try {
      render(<StreamProvider>test</StreamProvider>)

      const source = mockEventSourceInstances[0]
      // 断线 → 自动重连成功
      act(() => source.error())
      act(() => source.open())

      expect(mockReconcile).not.toHaveBeenCalled()
      act(() => vi.advanceTimersByTime(1000))

      // 水位线未前进（无重放）→ 对活跃会话兜底对账
      expect(mockReconcile).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('skips reconciliation when replayed events advanced the watermark', () => {
    vi.useFakeTimers()
    try {
      render(<StreamProvider>test</StreamProvider>)

      const source = mockEventSourceInstances[0]
      act(() => source.error())
      act(() => source.open())

      // 重连后重放帧先到，水位线前进
      mockWatermark = 42
      act(() => vi.advanceTimersByTime(1000))

      expect(mockReconcile).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not reconcile on the first connect (no prior disconnect)', () => {
    vi.useFakeTimers()
    try {
      render(<StreamProvider>test</StreamProvider>)

      const source = mockEventSourceInstances[0]
      act(() => source.open())
      act(() => vi.advanceTimersByTime(2000))

      expect(mockReconcile).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('StreamProvider unmount flush', () => {
  it('flushes pending events synchronously on unmount before closing EventSource', () => {
    const { unmount } = render(<StreamProvider>test</StreamProvider>)

    const source = mockEventSourceInstances[0]

    // Emit 3 events (before rAF fires)
    for (let i = 0; i < 3; i++) {
      source.emit({ type: 'part.delta', messageId: 'm1', partIndex: 0, delta: { type: 'text.append', text: `data${i}` } })
    }

    // Before unmount: applyEvent not called yet (rAF hasn't fired)
    expect(mockApplyEvent).not.toHaveBeenCalled()

    // Unmount before rAF fires
    unmount()

    // All 3 events should have been applied synchronously on unmount
    expect(mockApplyEvent).toHaveBeenCalledTimes(3)
    for (let i = 0; i < 3; i++) {
      const call = mockApplyEvent.mock.calls[i][0]
      expect(call.delta.text).toBe(`data${i}`)
    }

    // EventSource should be closed
    expect(source.closed).toBe(true)
  })
})
