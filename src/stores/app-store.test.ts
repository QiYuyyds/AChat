import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MessageRow } from '@/db/schema'
import type { DispatchPlanItem } from '@/shared/types'

import { selectDispatchForMessage, useAppStore } from './app-store'

const PLAN: DispatchPlanItem[] = [
  {
    id: 'task_frontend',
    agentId: 'ag_frontend',
    task: '实现页面调整',
  },
]

function resetStore(): void {
  useAppStore.setState({
    conversations: {},
    agents: {},
    messages: {},
    artifacts: {},
    messageIdsByConv: {},
    runsByConv: {},
    dispatchesByRunId: {},
    activeConversationId: null,
    previewArtifactId: null,
    fileExplorerOpen: false,
    openFilesByConv: {},
    activeTabByConv: {},
    replyTargetByConv: {},
    pendingQuoteForInput: null,
    pendingAttachmentsByConv: {},
    pendingWritesByConv: {},
    pendingBashCommandsByConv: {},
    pendingQuestionsByConv: {},
    unreadByConv: {},
    mobileSidebarOpen: false,
    highlightedMessageId: null,
    streamConnected: false,
  })
}

function agentMessage(id: string, runId: string, createdAt: number): MessageRow {
  return {
    id,
    conversationId: 'conv_1',
    role: 'agent',
    agentId: 'ag_orchestrator',
    parts: [],
    status: 'complete',
    parentMessageId: null,
    mentionedAgentIds: [],
    runId,
    usage: null,
    hidden: false,
    createdAt,
  }
}

describe('app-store dispatch plan binding', () => {
  beforeEach(() => {
    resetStore()
  })

  it('does not return the same dispatch for every message in the run', () => {
    useAppStore.setState({
      messages: {
        msg_plan: agentMessage('msg_plan', 'run_orch', 1),
        msg_extra: agentMessage('msg_extra', 'run_orch', 2),
      },
      dispatchesByRunId: {
        run_orch: {
          runId: 'run_orch',
          messageId: 'msg_plan',
          plan: PLAN,
          taskStatus: { task_frontend: 'pending' },
          childRunIds: {},
          reviewStatus: 'pending',
          pendingPlanId: 'pdp_1',
        },
      },
    })

    const state = useAppStore.getState()
    expect(selectDispatchForMessage(state, 'msg_plan')?.runId).toBe('run_orch')
    expect(selectDispatchForMessage(state, 'msg_extra')).toBeNull()
  })

  it('attaches a pending dispatch to the next message for that run', () => {
    useAppStore.getState().applyEvent({
      type: 'dispatch.plan.pending',
      conversationId: 'conv_1',
      timestamp: 1,
      pendingPlan: {
        id: 'pdp_1',
        conversationId: 'conv_1',
        agentId: 'ag_orchestrator',
        runId: 'run_orch',
        plan: PLAN,
        createdAt: 1,
      },
    })

    expect(useAppStore.getState().dispatchesByRunId.run_orch?.messageId).toBe('')

    useAppStore.getState().applyEvent({
      type: 'message.start',
      conversationId: 'conv_1',
      timestamp: 2,
      messageId: 'msg_plan',
      agentId: 'ag_orchestrator',
      runId: 'run_orch',
    })

    const state = useAppStore.getState()
    expect(state.dispatchesByRunId.run_orch?.messageId).toBe('msg_plan')
    expect(selectDispatchForMessage(state, 'msg_plan')?.pendingPlanId).toBe('pdp_1')
  })
})

describe('app-store run failure cleanup', () => {
  beforeEach(() => {
    resetStore()
  })

  it('adds error results for unresolved tool calls when a run fails', () => {
    useAppStore.setState({
      messages: {
        msg_tool: {
          ...agentMessage('msg_tool', 'run_failed', 1),
          status: 'streaming',
          parts: [
            {
              type: 'tool_use',
              callId: 'call_bash',
              toolName: 'bash',
              args: { command: 'npm run dev' },
            },
          ],
        },
      },
      messageIdsByConv: { conv_1: ['msg_tool'] },
    })

    useAppStore.getState().applyEvent({
      type: 'run.end',
      conversationId: 'conv_1',
      timestamp: 2,
      runId: 'run_failed',
      status: 'failed',
      error: 'process exited with code 1',
    })

    const message = useAppStore.getState().messages.msg_tool
    expect(message.status).toBe('error')
    expect(message.parts).toContainEqual({
      type: 'tool_result',
      callId: 'call_bash',
      result: '工具调用未完成：本次运行失败。process exited with code 1',
      isError: true,
    })

    useAppStore.getState().applyEvent({
      type: 'tool.result',
      conversationId: 'conv_1',
      timestamp: 3,
      messageId: 'msg_tool',
      callId: 'call_bash',
      result: 'server fallback',
      isError: true,
    })

    const results = useAppStore
      .getState()
      .messages.msg_tool.parts.filter((part) => part.type === 'tool_result')
    expect(results).toHaveLength(1)
    expect(results[0]).toEqual({
      type: 'tool_result',
      callId: 'call_bash',
      result: 'server fallback',
      isError: true,
      endedAt: 3,
    })
  })

  it('does not duplicate existing tool results on aborted runs', () => {
    useAppStore.setState({
      messages: {
        msg_tool: {
          ...agentMessage('msg_tool', 'run_aborted', 1),
          status: 'streaming',
          parts: [
            {
              type: 'tool_use',
              callId: 'call_done',
              toolName: 'fs_read',
              args: { path: 'README.md' },
            },
            {
              type: 'tool_result',
              callId: 'call_done',
              result: 'ok',
              isError: false,
            },
          ],
        },
      },
      messageIdsByConv: { conv_1: ['msg_tool'] },
    })

    useAppStore.getState().applyEvent({
      type: 'run.end',
      conversationId: 'conv_1',
      timestamp: 2,
      runId: 'run_aborted',
      status: 'aborted',
    })

    const message = useAppStore.getState().messages.msg_tool
    expect(message.status).toBe('aborted')
    expect(message.parts.filter((part) => part.type === 'tool_result')).toHaveLength(1)
  })
})

describe('app-store timestamp capture', () => {
  beforeEach(() => {
    resetStore()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('part.start captures startedAt for thinking parts', () => {
    useAppStore.getState().applyEvent({
      type: 'message.start',
      conversationId: 'conv_1',
      timestamp: 1000,
      messageId: 'msg_ts',
      agentId: 'ag_test',
      runId: 'run_test',
    })

    useAppStore.getState().applyEvent({
      type: 'part.start',
      conversationId: 'conv_1',
      timestamp: 2000,
      messageId: 'msg_ts',
      partIndex: 0,
      part: { type: 'thinking', content: '' },
    })

    const msg = useAppStore.getState().messages.msg_ts
    expect(msg.parts[0]).toMatchObject({ type: 'thinking', startedAt: 2000 })
  })

  it('part.end captures endedAt for thinking parts', () => {
    useAppStore.setState({
      messages: {
        msg_ts: {
          ...agentMessage('msg_ts', 'run_test', 1000),
          status: 'streaming',
          parts: [{ type: 'thinking', content: 'hello', startedAt: 2000 }],
        },
      },
    })

    useAppStore.getState().applyEvent({
      type: 'part.end',
      conversationId: 'conv_1',
      timestamp: 5000,
      messageId: 'msg_ts',
      partIndex: 0,
    })

    const part = useAppStore.getState().messages.msg_ts.parts[0]
    expect(part).toMatchObject({ type: 'thinking', endedAt: 5000 })
  })

  it('tool.call captures startedAt', () => {
    useAppStore.setState({
      messages: {
        msg_ts: {
          ...agentMessage('msg_ts', 'run_test', 1000),
          status: 'streaming',
          parts: [],
        },
      },
    })

    useAppStore.getState().applyEvent({
      type: 'tool.call',
      conversationId: 'conv_1',
      timestamp: 3000,
      messageId: 'msg_ts',
      callId: 'call_1',
      toolName: 'bash',
      args: { command: 'ls' },
    })

    const part = useAppStore.getState().messages.msg_ts.parts[0]
    expect(part).toMatchObject({ type: 'tool_use', startedAt: 3000 })
  })

  it('tool.result captures endedAt', () => {
    useAppStore.setState({
      messages: {
        msg_ts: {
          ...agentMessage('msg_ts', 'run_test', 1000),
          status: 'streaming',
          parts: [
            { type: 'tool_use', callId: 'call_1', toolName: 'bash', args: {}, startedAt: 3000 },
          ],
        },
      },
    })

    useAppStore.getState().applyEvent({
      type: 'tool.result',
      conversationId: 'conv_1',
      timestamp: 6000,
      messageId: 'msg_ts',
      callId: 'call_1',
      result: 'done',
      isError: false,
    })

    const result = useAppStore
      .getState()
      .messages.msg_ts.parts.find((p) => p.type === 'tool_result')
    expect(result).toMatchObject({ type: 'tool_result', endedAt: 6000 })
  })
})

describe('app-store worktree event handling', () => {
  beforeEach(() => {
    resetStore()
  })

  it('worktree.created populates worktreeByTask with branchName and path', () => {
    useAppStore.setState({
      dispatchesByRunId: {
        run_orch: {
          runId: 'run_orch',
          messageId: 'msg_plan',
          plan: PLAN,
          taskStatus: { task_frontend: 'running' },
          childRunIds: {},
        },
      },
    })

    useAppStore.getState().applyEvent({
      type: 'worktree.created',
      conversationId: 'conv_1',
      timestamp: 1,
      taskId: 'task_frontend',
      branchName: 'agent/code-writer/t1',
      path: '/data/worktrees/conv_1/task_frontend',
    })

    const wt = useAppStore.getState().dispatchesByRunId.run_orch?.worktreeByTask?.task_frontend
    expect(wt).toBeDefined()
    expect(wt?.branchName).toBe('agent/code-writer/t1')
    expect(wt?.path).toBe('/data/worktrees/conv_1/task_frontend')
    expect(wt?.mergeStatus).toBeUndefined()
  })

  it('worktree.merged updates mergeStatus to success', () => {
    useAppStore.setState({
      dispatchesByRunId: {
        run_orch: {
          runId: 'run_orch',
          messageId: 'msg_plan',
          plan: PLAN,
          taskStatus: { task_frontend: 'running' },
          childRunIds: {},
          worktreeByTask: {
            task_frontend: {
              branchName: 'agent/code-writer/t1',
              path: '/data/worktrees/conv_1/task_frontend',
            },
          },
        },
      },
    })

    useAppStore.getState().applyEvent({
      type: 'worktree.merged',
      conversationId: 'conv_1',
      timestamp: 2,
      taskId: 'task_frontend',
      mergeStatus: 'success',
    })

    const wt = useAppStore.getState().dispatchesByRunId.run_orch?.worktreeByTask?.task_frontend
    expect(wt?.mergeStatus).toBe('success')
  })

  it('worktree.merged with conflict sets task status to merge_conflict', () => {
    useAppStore.setState({
      dispatchesByRunId: {
        run_orch: {
          runId: 'run_orch',
          messageId: 'msg_plan',
          plan: PLAN,
          taskStatus: { task_frontend: 'running' },
          childRunIds: {},
          worktreeByTask: {
            task_frontend: {
              branchName: 'agent/code-writer/t1',
              path: '/data/worktrees/conv_1/task_frontend',
            },
          },
        },
      },
    })

    useAppStore.getState().applyEvent({
      type: 'worktree.merged',
      conversationId: 'conv_1',
      timestamp: 2,
      taskId: 'task_frontend',
      mergeStatus: 'conflict',
    })

    const state = useAppStore.getState().dispatchesByRunId.run_orch!
    expect(state.worktreeByTask?.task_frontend?.mergeStatus).toBe('conflict')
    expect(state.taskStatus.task_frontend).toBe('merge_conflict')
  })

  it('worktree.cleaned removes worktree data for the task', () => {
    useAppStore.setState({
      dispatchesByRunId: {
        run_orch: {
          runId: 'run_orch',
          messageId: 'msg_plan',
          plan: PLAN,
          taskStatus: { task_frontend: 'complete' },
          childRunIds: {},
          worktreeByTask: {
            task_frontend: {
              branchName: 'agent/code-writer/t1',
              path: '/data/worktrees/conv_1/task_frontend',
              mergeStatus: 'success',
            },
          },
        },
      },
    })

    useAppStore.getState().applyEvent({
      type: 'worktree.cleaned',
      conversationId: 'conv_1',
      timestamp: 3,
      taskId: 'task_frontend',
    })

    const wt = useAppStore.getState().dispatchesByRunId.run_orch?.worktreeByTask?.task_frontend
    expect(wt).toBeUndefined()
  })
})
