'use client'

import { useEffect } from 'react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useSessionNoteStore } from '@/stores/session-note-store'
import type { SessionNote } from '@/shared/session-note'

interface SessionNotePanelProps {
  conversationId: string
}

function SectionList({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li
            key={`${title}-${i}`}
            className="rounded-md bg-muted/50 px-2 py-1 text-sm"
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

function NoteContent({ note }: { note: SessionNote }) {
  return (
    <div className="space-y-4">
      {note.currentState && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">当前状态</p>
          <p className="text-sm">{note.currentState}</p>
        </div>
      )}

      <SectionList title="关键决策" items={note.keyDecisions} />
      <SectionList title="操作文件" items={note.filesTouched} />
      <SectionList title="执行命令" items={note.commandsRun} />
      <SectionList title="产出物" items={note.artifactsProduced} />
      <SectionList title="阻塞项" items={note.blockers} />
      <SectionList title="待解决问题" items={note.openQuestions} />
      <SectionList title="下一步" items={note.nextSteps} />

      {note.architectureUnderstanding && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">架构理解</p>
          <p className="whitespace-pre-line text-sm">
            {note.architectureUnderstanding}
          </p>
        </div>
      )}
    </div>
  )
}

function LoadingState() {
  return (
    <div className="space-y-3">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="space-y-2">
          <div className="h-3 w-20 animate-pulse rounded bg-muted" />
          <div className="h-4 w-full animate-pulse rounded bg-muted" />
          <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  )
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 py-8 text-center">
      <p className="text-sm text-destructive">加载会话笔记失败</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:bg-primary/90"
      >
        重试
      </button>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 py-12 text-center">
      <p className="text-sm text-muted-foreground">暂无会话笔记</p>
    </div>
  )
}

export function SessionNotePanel({ conversationId }: SessionNotePanelProps) {
  const { note, loading, error, fetchNote, clear } = useSessionNoteStore()

  useEffect(() => {
    clear()
    void fetchNote(conversationId)
    return () => clear()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  return (
    <Card className="h-full">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-base">会话笔记</CardTitle>
        {note?.title && (
          <Badge variant="secondary">{note.title}</Badge>
        )}
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[calc(100vh-200px)] min-h-[300px]">
          {loading ? (
            <LoadingState />
          ) : error ? (
            <ErrorState onRetry={() => void fetchNote(conversationId)} />
          ) : note ? (
            <NoteContent note={note} />
          ) : (
            <EmptyState />
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
