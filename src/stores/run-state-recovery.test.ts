import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConversationRunRow } from '@/lib/api'
import type { DispatchPlanItem, StreamEvent } from '@/shared/types'

import { fetchConversationRuns } from '@/lib/api'

import { applyRunSnapshot, selectDispatchForMessage, useAppStore } from './app-store'
import type { DispatchState, RunState } from './app-store'

// 运行态重建走 REST（fetchConversationRuns），测试里 mock 掉网络层。
vi.mock('@/lib/api', () => ({
  fetchMessages: vi.fn(),
  fetchPendingSummary: vi.fn(),
  fetchConversationRuns: vi.fn(),
}))

const PLAN: DispatchPlanItem[] = [
  { id: 'task_a', agentId: 'ag_x', task: 'A' },
  { id: 'task_b', agentId: 'ag_y', task: 'B', dependsOn: ['task_a'] },
]

function runRow(overrides: Partial<ConversationRunRow> = {}): ConversationRunRow {
  return {
    id: 'run_1',
    conversationId: 'conv_1',
    agentId: 'ag_orch',
    triggerMessageId: null,
    status: 'running',
    error: null,
    parentRunId: null,
    usage: null,
    startedAt: 1000,
    finishedAt: null,
    stopReason: null,
    stopReasonLabel: null,
    dispatchPlan: null,
    dispatchResults: null,
    ...overrides,
  }
}

function orchestratorMessage(id: string, runId: string, createdAt: number) {
  return {
    id,
    conversationId: 'conv_1',
    role: 'agent' as const,
    agentId: 'ag_orch',
    parts: [],
    status: 'complete' as const,
    parentMessageId: null,
    mentionedAgentIds: [] as string[],
    runId,
    usage: null,
    hidden: false,
    createdAt,
  }
}

function freshState() {
  return {
    runsByConv: {} as Record<string, Record<string, RunState>>,
    dispatchesByRunId: {} as Record<string, DispatchState>,
    messages: {} as Record<string, ReturnType<typeof orchestratorMessage>>,
  }
}

describe('applyRunSnapshot (run-state recovery rebuild)', () => {
  it('empty snapshot rebuilds an empty bucket and no dispatches', () => {
    const state = freshState()
    applyRunSnapshot(state as never, 'conv_1', [])
    expect(state.runsByConv.conv_1).toEqual({})
    expect(state.dispatchesByRunId).toEqual({})
  })

  it('active run: rebuilds RunState and DispatchState aligned with the SSE build path', () => {
    const state = freshState()
    state.messages['msg_plan'] = orchestratorMessage('msg_plan', 'run_1', 10)
    state.messages['msg_orphan_run'] = orchestratorMessage('msg_other', 'run_other', 20)
    applyRunSnapshot(state as never, 'conv_1', [
      runRow({
        dispatchPlan: { dagId: 'dag1', visibility: 'hidden', tasks: PLAN },
        dispatchResults: {
          taskStatus: { task_a: 'complete', task_b: 'running' },
          childRunIds: { task_a: 'run_child_a' },
        },
      }),
    ])

    const run = state.runsByConv.conv_1.run_1
    expect(run).toMatchObject({
      id: 'run_1',
      status: 'running',
      startedAt: 1000,
      parentRunId: null,
    })

    const dispatch = state.dispatchesByRunId.run_1
    expect(dispatch).toBeDefined()
    expect(dispatch.messageId).toBe('msg_plan')
    expect(dispatch.plan).toEqual(PLAN)
    // persisted progress wins over the plan default; unknown tasks fall back to pending
    expect(dispatch.taskStatus).toEqual({ task_a: 'complete', task_b: 'running' })
    expect(dispatch.childRunIds).toEqual({ task_a: 'run_child_a' })
    expect(dispatch.reviewStatus).toBe('approved')

    // 中止按钮：running 且顶层 → useTopLevelRunningRuns 的谓词命中
    const topLevelRunning = Object.values(state.runsByConv.conv_1).filter(
      (r) => (r.status === 'running' || r.status === 'queued') && !r.parentRunId,
    )
    expect(topLevelRunning.map((r) => r.id)).toEqual(['run_1'])
  })

  it('orphaned run (restart residue): terminal status; stuck running tasks coerced to aborted', () => {
    const state = freshState()
    applyRunSnapshot(state as never, 'conv_1', [
      runRow({
        status: 'interrupted',
        finishedAt: 2000,
        error: 'interrupted by restart',
        dispatchPlan: { dagId: 'dag1', visibility: 'hidden', tasks: PLAN },
        dispatchResults: {
          taskStatus: { task_a: 'complete', task_b: 'running' },
          childRunIds: { task_a: 'run_child_a', task_b: 'run_child_b' },
        },
      }),
    ])

    const run = state.runsByConv.conv_1.run_1
    expect(run.status).toBe('interrupted')
    expect(run.turnMetricsComplete).toBe(true)

    const dispatch = state.dispatchesByRunId.run_1
    expect(dispatch.taskStatus).toEqual({ task_a: 'complete', task_b: 'aborted' })
  })

  it('skips the rebuild when local run state already exists (no duplicate build with SSE events)', () => {
    const state = freshState()
    state.runsByConv.conv_1 = {}
    // SSE 已经构建了运行态（run.start + dispatch.plan 到达过）
    state.runsByConv.conv_1.run_live = {
      id: 'run_live',
      conversationId: 'conv_1',
      agentId: 'ag_orch',
      triggerMessageId: null,
      status: 'running',
      error: null,
      parentRunId: null,
      usage: null,
      startedAt: 5,
      finishedAt: null,
      turnMetrics: {},
      turnMetricsComplete: false,
      stopReason: null,
      stopReasonLabel: null,
    }
    state.dispatchesByRunId.run_live = {
      runId: 'run_live',
      messageId: 'msg_live',
      plan: [{ id: 'task_live', agentId: 'ag_x', task: 'live' }],
      taskStatus: { task_live: 'running' },
      childRunIds: {},
      reviewStatus: 'approved',
    }

    applyRunSnapshot(state as never, 'conv_1', [runRow()])
    // 本地运行态原样保留，未被快照覆盖
    expect(Object.keys(state.runsByConv.conv_1)).toEqual(['run_live'])
    expect(state.dispatchesByRunId.run_live.taskStatus).toEqual({ task_live: 'running' })
  })

  it('overwrite mode (reconcile): replaces the bucket and drops phantom dispatches', () => {
    const state = freshState()
    state.runsByConv.conv_1 = {}
    state.runsByConv.conv_1.run_phantom = {
      id: 'run_phantom',
      conversationId: 'conv_1',
      agentId: 'ag_orch',
      triggerMessageId: null,
      status: 'running',
      error: null,
      parentRunId: null,
      usage: null,
      startedAt: 1,
      finishedAt: null,
      turnMetrics: {},
      turnMetricsComplete: false,
      stopReason: null,
      stopReasonLabel: null,
    }
    state.dispatchesByRunId.run_phantom = {
      runId: 'run_phantom',
      messageId: 'msg_phantom',
      plan: PLAN,
      taskStatus: { task_a: 'running' },
      childRunIds: {},
      reviewStatus: 'approved',
    }

    applyRunSnapshot(state as never, 'conv_1', [runRow()], { overwrite: true })
    expect(Object.keys(state.runsByConv.conv_1)).toEqual(['run_1'])
    expect(state.dispatchesByRunId.run_phantom).toBeUndefined()
  })

  it('selectDispatchForMessage falls back to the runId link when messageId is empty', () => {
    const state = freshState()
    state.messages['msg_late'] = orchestratorMessage('msg_late', 'run_1', 30)
    state.dispatchesByRunId.run_1 = {
      runId: 'run_1',
      messageId: '',
      plan: PLAN,
      taskStatus: {},
      childRunIds: {},
      reviewStatus: 'approved',
    }
    const picked = selectDispatchForMessage(state as never, 'msg_late')
    expect(picked?.runId).toBe('run_1')
  })
})

describe('ensureRunStateRebuilt store action', () => {
  beforeEach(() => {
    useAppStore.setState({
      conversations: {},
      agents: {},
      messages: {},
      artifacts: {},
      messageIdsByConv: {},
      runsByConv: {},
      runSnapshotLoadedByConv: {},
      dispatchesByRunId: {},
      activeConversationId: null,
    })
    vi.mocked(fetchConversationRuns).mockReset()
  })

  it('fetches once, rebuilds state, and marks the snapshot loaded', async () => {
    vi.mocked(fetchConversationRuns).mockResolvedValue([
      runRow({ dispatchPlan: { dagId: 'dag1', visibility: 'hidden', tasks: PLAN } }),
    ])

    await useAppStore.getState().ensureRunStateRebuilt('conv_1')

    expect(useAppStore.getState().runSnapshotLoadedByConv.conv_1).toBe(true)
    expect(useAppStore.getState().runsByConv.conv_1?.run_1).toBeDefined()
    expect(useAppStore.getState().dispatchesByRunId.run_1).toBeDefined()

    // 二次调用不再发请求（幂等）
    await useAppStore.getState().ensureRunStateRebuilt('conv_1')
    expect(fetchConversationRuns).toHaveBeenCalledTimes(1)
  })

  it('a run.end SSE event after the rebuild still lands on the rebuilt run', async () => {
    vi.mocked(fetchConversationRuns).mockResolvedValue([runRow()])
    await useAppStore.getState().ensureRunStateRebuilt('conv_1')

    useAppStore.getState().applyEvent({
      type: 'run.end',
      conversationId: 'conv_1',
      runId: 'run_1',
      status: 'complete',
      timestamp: 5000,
    } as unknown as StreamEvent)

    expect(useAppStore.getState().runsByConv.conv_1?.run_1?.status).toBe('complete')
  })
})
