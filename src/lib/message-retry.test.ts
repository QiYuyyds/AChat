import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MessageRow } from '@/db/schema'
import type { SendMessageResult } from '@/lib/api'
import { sendMessage } from '@/lib/api'
import { useAppStore } from '@/stores/app-store'

import { buildRetryPayload, retryFailedMessage } from './message-retry'

vi.mock('@/lib/api', () => ({ sendMessage: vi.fn() }))

const CONV = 'conv_1'
const TEMP_ID = 'temp_retry1'

function tempUserMessage(overrides: Partial<MessageRow> = {}): MessageRow {
  return {
    id: TEMP_ID,
    conversationId: CONV,
    role: 'user',
    agentId: null,
    parts: [{ type: 'text', content: '你好 @orchestrator' }],
    status: 'error',
    parentMessageId: null,
    mentionedAgentIds: [],
    runId: null,
    usage: null,
    hidden: false,
    createdAt: Date.now(),
    ...overrides,
  }
}

function seedMessage(msg: MessageRow): void {
  const s = useAppStore.getState()
  useAppStore.setState({
    messages: { ...s.messages, [msg.id]: msg },
    messageIdsByConv: {
      ...s.messageIdsByConv,
      [msg.conversationId]: [...(s.messageIdsByConv[msg.conversationId] ?? []), msg.id],
    },
  })
}

beforeEach(() => {
  useAppStore.setState({ messages: {}, messageIdsByConv: {} })
  vi.mocked(sendMessage).mockReset()
})

describe('buildRetryPayload', () => {
  it('重建文本 + @ 提及 + 引用回复 + 附件 payload', () => {
    const msg = tempUserMessage({
      parts: [
        { type: 'text', content: '看这个文件' },
        { type: 'image_attachment', attachmentId: 'att_img', fileName: 'a.png', size: 1, mimeType: 'image/png' },
        { type: 'file_attachment', attachmentId: 'att_doc', fileName: 'b.pdf', size: 2, mimeType: 'application/pdf' },
      ],
      mentionedAgentIds: ['ag_1', 'ag_2'],
      parentMessageId: 'msg_parent',
    })
    expect(buildRetryPayload(msg)).toEqual({
      content: '看这个文件',
      mentionedAgentIds: ['ag_1', 'ag_2'],
      parentMessageId: 'msg_parent',
      attachmentIds: ['att_img', 'att_doc'],
    })
  })

  it('无引用回复时不携带 parentMessageId', () => {
    const payload = buildRetryPayload(tempUserMessage())
    expect(payload.parentMessageId).toBeUndefined()
    expect(payload.content).toBe('你好 @orchestrator')
  })
})

describe('retryFailedMessage', () => {
  it('成功：payload 重建 + clientMessageId 复用 tempId + 认领/替换链路', async () => {
    const realRow = tempUserMessage({ id: 'msg_real', status: 'complete' })
    vi.mocked(sendMessage).mockResolvedValueOnce({
      messageId: 'msg_real',
      runIds: [],
      messages: [realRow],
    })
    seedMessage(tempUserMessage())

    await retryFailedMessage(CONV, TEMP_ID)

    expect(sendMessage).toHaveBeenCalledWith(CONV, {
      content: '你好 @orchestrator',
      mentionedAgentIds: [],
      parentMessageId: undefined,
      attachmentIds: [],
      clientMessageId: TEMP_ID,
    })
    expect(useAppStore.getState().messages[TEMP_ID]).toBeUndefined()
    expect(useAppStore.getState().messages['msg_real']).toEqual(realRow)
    expect(useAppStore.getState().messageIdsByConv[CONV]).toEqual(['msg_real'])
  })

  it('防重入：in-flight 期间第二次调用直接 no-op，不发第二次请求', async () => {
    let resolveSend!: (value: SendMessageResult) => void
    vi.mocked(sendMessage).mockImplementationOnce(
      () => new Promise<SendMessageResult>((res) => { resolveSend = res }),
    )
    seedMessage(tempUserMessage())

    const first = retryFailedMessage(CONV, TEMP_ID)
    // 发起后 status 已复位为乐观态（与初始乐观消息同构），失败态样式随之中断
    expect(useAppStore.getState().messages[TEMP_ID]?.status).toBe('complete')

    const second = retryFailedMessage(CONV, TEMP_ID)
    expect(sendMessage).toHaveBeenCalledTimes(1)

    resolveSend({ messageId: 'msg_real', runIds: [] })
    await Promise.all([first, second])
    expect(sendMessage).toHaveBeenCalledTimes(1)
    expect(useAppStore.getState().messages[TEMP_ID]).toBeUndefined()
  })

  it('重试失败：temp 未被认领时重新标 error，重试入口恢复', async () => {
    vi.mocked(sendMessage).mockRejectedValueOnce(new Error('network down'))
    seedMessage(tempUserMessage())

    await retryFailedMessage(CONV, TEMP_ID)

    expect(useAppStore.getState().messages[TEMP_ID]?.status).toBe('error')
  })

  it('重试失败但 temp 已被 message.added 认领时不再标 error', async () => {
    vi.mocked(sendMessage).mockRejectedValueOnce(new Error('network down'))
    seedMessage(tempUserMessage())

    const pending = retryFailedMessage(CONV, TEMP_ID)
    // 模拟 SSE message.added 抢先认领：temp 被替换为真实消息行
    useAppStore.getState().replaceLocalMessageId(TEMP_ID, 'msg_claimed')
    await pending

    expect(useAppStore.getState().messages['msg_claimed']?.status).toBe('complete')
    expect(useAppStore.getState().messages[TEMP_ID]).toBeUndefined()
  })

  it('非 error 态或不存在的消息不触发重试请求', async () => {
    seedMessage(tempUserMessage({ status: 'complete' }))
    await retryFailedMessage(CONV, TEMP_ID)
    await retryFailedMessage(CONV, 'temp_missing')
    expect(sendMessage).not.toHaveBeenCalled()
  })
})
