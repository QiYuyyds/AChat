'use client'

import { Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import type { SkillSummary } from '@/lib/api'

interface SkillDetailDialogProps {
  skill: SkillSummary
  open: boolean
  onOpenChange: (open: boolean) => void
  onDelete: () => void
}

export function SkillDetailDialog({ skill, open, onOpenChange, onDelete }: SkillDetailDialogProps) {
  const [skillContent, setSkillContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)

  useEffect(() => {
    if (!open) {
      setSkillContent(null)
      return
    }

    setLoading(true)
    fetch(`/api/skills/${skill.slug}`)
      .then((res) => {
        if (!res.ok) throw new Error('Failed to fetch skill')
        return res.json()
      })
      .then((data) => {
        setSkillContent(data.content || '暂无内容')
      })
      .catch((err) => {
        console.error('[SkillDetailDialog] fetch failed', err)
        setSkillContent('加载失败')
      })
      .finally(() => setLoading(false))
  }, [open, skill.slug])

  const handleDelete = () => {
    onDelete()
    setDeleteConfirmOpen(false)
    onOpenChange(false)
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          className="flex h-auto max-h-[80vh] flex-col overflow-hidden p-0 sm:max-w-3xl"
        >
          {/* Header with accent */}
          <DialogHeader className="shrink-0 border-b px-6 pb-4 pt-6">
            <div className="flex items-start gap-3">
              <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-amber-500/10 text-amber-500">
                <Sparkles className="size-5.5" />
              </div>
              <div className="min-w-0 flex-1">
                <DialogTitle className="text-lg tracking-tight">{skill.name}</DialogTitle>
                <div className="mt-1.5 flex items-center gap-2">
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {skill.slug}
                  </code>
                  {skill.triggerKeywords && skill.triggerKeywords.length > 0 && (
                    <div className="flex items-center gap-1">
                      {skill.triggerKeywords.slice(0, 4).map((kw) => (
                        <span
                          key={kw}
                          className="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
                        >
                          {kw}
                        </span>
                      ))}
                      {skill.triggerKeywords.length > 4 && (
                        <span className="text-[10px] text-muted-foreground">
                          +{skill.triggerKeywords.length - 4}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </DialogHeader>

          {/* Body */}
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-5 px-6 py-5">
              {/* Description */}
              {skill.description && (
                <div>
                  <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    描述
                  </h4>
                  <p className="mt-2 text-sm leading-relaxed">{skill.description}</p>
                </div>
              )}

              {/* Content */}
              <div>
                <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  SKILL.md 内容
                </h4>
                <div className="mt-2 overflow-hidden rounded-xl border bg-muted/30">
                  {loading ? (
                    <div className="flex items-center justify-center py-12 text-muted-foreground">
                      <div className="size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                      <span className="ml-2 text-sm">加载中…</span>
                    </div>
                  ) : skillContent ? (
                    <pre
                      className={cn(
                        'max-h-[50vh] overflow-auto whitespace-pre-wrap p-4',
                        'font-mono text-xs leading-relaxed text-foreground/90',
                      )}
                    >
                      {skillContent}
                    </pre>
                  ) : (
                    <p className="p-4 text-sm text-muted-foreground">暂无内容</p>
                  )}
                </div>
              </div>
            </div>
          </ScrollArea>

          {/* Footer */}
          <div className="shrink-0 border-t px-6 py-4">
            <div className="flex items-center justify-between">
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteConfirmOpen(true)}
                className="gap-1.5"
              >
                <Trash2 className="size-3.5" />
                删除
              </Button>
              <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>删除技能</DialogTitle>
            <DialogDescription>
              确定要删除「{skill.name}」吗？此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDeleteConfirmOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
