import type { MessageRow } from '@/db/schema'
import { sendMessage } from '@/lib/api'
import { useAppStore } from '@/stores/app-store'

/**
 * 发送失败重试：从本地 temp 消息记录重建发送 payload，clientMessageId 复用原 tempId，
 * 让重试引发的 message.added 事件认领同一个乐观气泡（replaceLocalMessageId 链路原样复用），
 * 不产生第二行。
 */

/** 重试 in-flight 的 tempId 集合：防并发双发，in-flight 期间的再次调用直接 no-op */
const retryingTempIds = new Set<string>()

export interface RetryPayload {
  content: string
  mentionedAgentIds: string[]
  parentMessageId?: string
  attachmentIds: string[]
}

/**
 * 从本地乐观消息记录重建发送 payload（纯函数）。
 * content 取自 text parts，附件取自 image/file_attachment parts 的 attachmentId。
 * 不重走输入框的 slash / quote 一次性处理 —— 直接以首发已构建的 finalContent 重发，
 * 与原消息内容完全一致。
 */
export function buildRetryPayload(message: MessageRow): RetryPayload {
  let content = ''
  const attachmentIds: string[] = []
  for (const part of message.parts) {
    if (part.type === 'text') {
      content += part.content
    } else if (part.type === 'image_attachment' || part.type === 'file_attachment') {
      attachmentIds.push(part.attachmentId)
    }
  }
  return {
    content,
    mentionedAgentIds: message.mentionedAgentIds,
    parentMessageId: message.parentMessageId ?? undefined,
    attachmentIds,
  }
}

/**
 * 重试一条发送失败的用户消息。
 *
 * - 仅对仍是 error 态的本地消息生效；不存在 / 非 error / 重试中时直接 no-op。
 * - 发起时先把 status 复位为乐观态（与初始乐观消息同构），失败态标注与重试按钮随即消失。
 * - 成功：replaceLocalMessageId + 回写服务端返回的消息行（与首发路径一致）。
 * - 失败：temp 未被 message.added 认领时重新标 error，重试入口恢复，可无限次重试。
 */
export async function retryFailedMessage(conversationId: string, tempId: string): Promise<void> {
  if (retryingTempIds.has(tempId)) return
  const failed = useAppStore.getState().messages[tempId]
  if (!failed || failed.role !== 'user' || failed.status !== 'error') return

  const payload = buildRetryPayload(failed)
  if (!payload.content && payload.attachmentIds.length === 0) return

  retryingTempIds.add(tempId)
  useAppStore.getState().upsertMessage({ ...failed, status: 'complete' })

  try {
    const result = await sendMessage(conversationId, {
      ...payload,
      // clientMessageId 复用原 tempId：message.added 事件携带它即时认领同一个乐观气泡
      clientMessageId: tempId,
    })
    useAppStore.getState().replaceLocalMessageId(tempId, result.messageId)
    for (const message of result.messages ?? []) {
      useAppStore.getState().upsertMessage(message)
    }
  } catch (err) {
    console.error('[message-retry] retry failed', err)
    // POST 失败：temp 已被 message.added 认领（后端实际已落库）时 messages[tempId] 为空，跳过；
    // 否则重新标 error，重试入口恢复。
    const temp = useAppStore.getState().messages[tempId]
    if (temp) useAppStore.getState().upsertMessage({ ...temp, status: 'error' })
  } finally {
    retryingTempIds.delete(tempId)
  }
}
