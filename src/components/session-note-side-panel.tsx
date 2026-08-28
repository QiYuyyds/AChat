'use client'

import { NotebookPen, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useActiveConversation, useAppStore } from '@/stores/app-store'

import { SessionNotePanel } from './session-note-panel'

export function SessionNoteSidePanel() {
  const conv = useActiveConversation()
  const open = useAppStore((s) => s.sessionNoteOpen)
  const setOpen = useAppStore((s) => s.setSessionNoteOpen)

  if (!open || !conv) return null

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l bg-card max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full max-md:animate-in max-md:slide-in-from-right max-md:duration-200">
      <header className="shrink-0 border-b px-3 py-2">
        <div className="flex items-center justify-between">
          <div className="flex min-w-0 items-center gap-1.5">
            <NotebookPen className="size-4 shrink-0 text-muted-foreground" />
            <span className="text-sm font-medium">会话笔记</span>
          </div>
          <Button
            size="icon"
            variant="ghost"
            onClick={() => setOpen(false)}
            title="关闭"
            aria-label="关闭"
          >
            <X className="size-4" />
          </Button>
        </div>
      </header>
      <ScrollArea className="min-h-0 flex-1">
        <div className="p-3">
          <SessionNotePanel conversationId={conv.id} />
        </div>
      </ScrollArea>
    </aside>
  )
}
