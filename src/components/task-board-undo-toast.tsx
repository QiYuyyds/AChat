'use client'

import { useEffect } from 'react'
import { Undo2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useAppStore } from '@/stores/app-store'

export function TaskBoardUndoToast() {
  const undoStack = useAppStore((s) => s.undoStack)
  const popUndo = useAppStore((s) => s.popUndo)
  const clearUndoStack = useAppStore((s) => s.clearUndoStack)

  const latest = undoStack[undoStack.length - 1]

  useEffect(() => {
    if (!latest) return
    const timer = setTimeout(() => {
      clearUndoStack()
    }, 5000)
    return () => clearTimeout(timer)
  }, [latest, clearUndoStack])

  if (!latest) return null

  return (
    <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 task-fade-up">
      <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-2.5 shadow-[var(--shadow-md)]">
        <span className="text-xs text-muted-foreground">{latest.message}</span>
        <Button
          variant="ghost"
          size="xs"
          onClick={() => popUndo()}
          className="text-primary"
        >
          <Undo2 className="mr-1 size-3" />
          撤销
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => clearUndoStack()}
        >
          <X className="size-3" />
        </Button>
      </div>
    </div>
  )
}
