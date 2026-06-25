import { ChevronRight } from "lucide-react"

import { AvatarBadge, conversationAvatarLabel } from "../lib/avatar"
import { formatTime } from "../lib/format"
import type { MobileConversationSummary } from "../types"

/** A single conversation list row — shared by the Status dashboard and the conversation list. */
export function ConversationRow({
  conv,
  onOpen,
}: {
  conv: MobileConversationSummary
  onOpen: (id: string) => void
}) {
  const hasBadges =
    conv.runningRunCount > 0 || conv.pendingWriteCount > 0 || conv.pendingQuestionCount > 0

  return (
    <button type="button" className="row conv-row" onClick={() => onOpen(conv.id)}>
      <AvatarBadge
        className="conversation-avatar"
        label={conversationAvatarLabel(conv.title, conv.mode)}
        toneKey={conv.id}
      />
      <div className="row-main">
        <span className="row-title">{conv.title}</span>
        <span className="row-sub">
          {conv.mode === "group" ? "群聊" : "单聊"} · {formatTime(conv.updatedAt)}
        </span>
        {hasBadges && (
          <div className="conv-badges">
            {conv.runningRunCount > 0 && <span className="mini-pill">运行 {conv.runningRunCount}</span>}
            {conv.pendingWriteCount > 0 && <span className="mini-pill">审批 {conv.pendingWriteCount}</span>}
            {conv.pendingQuestionCount > 0 && (
              <span className="mini-pill">提问 {conv.pendingQuestionCount}</span>
            )}
          </div>
        )}
      </div>
      <ChevronRight className="chevron-icon" aria-hidden="true" />
    </button>
  )
}
