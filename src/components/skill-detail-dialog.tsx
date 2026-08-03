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
import { Badge } from '@/components/ui/badge'
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

    // Fetch skill content
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
        <DialogContent className="w-[95vw] sm:w-[90vw] lg:w-[85vw] xl:w-[80vw] h-auto max-h-[80vh] flex flex-col overflow-hidden p-0" style={{ maxWidth: '1200px' }}>
          <DialogHeader className="shrink-0 px-6 pt-6 pb-2">
            <div className="flex items-start gap-3">
              <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-amber-500/10 text-amber-500">
                <Sparkles className="size-5" />
              </div>
              <div className="min-w-0 flex-1">
                <DialogTitle className="text-lg">{skill.name}</DialogTitle>
                <DialogDescription className="mt-1">
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{skill.slug}</code>
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>

          <div className="flex-1 min-h-0 overflow-y-auto px-6">
            <div className="space-y-4 py-4">
              {/* Description */}
              <div>
                <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">描述</h4>
                <p className="mt-1.5 text-sm">{skill.description}</p>
              </div>

              {/* Trigger Keywords */}
              {skill.triggerKeywords && skill.triggerKeywords.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">触发关键词</h4>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {skill.triggerKeywords.map((kw) => (
                      <Badge key={kw} variant="secondary" className="text-xs">
                        {kw}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Content */}
              <div>
                <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">SKILL.md 内容</h4>
                <div className="mt-1.5 rounded-lg border bg-muted/50 p-4">
                  {loading ? (
                    <div className="flex items-center justify-center py-8 text-muted-foreground">
                      <div className="size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                      <span className="ml-2 text-sm">加载中...</span>
                    </div>
                  ) : skillContent ? (
                    <pre className="whitespace-pre-wrap text-xs leading-relaxed font-mono text-foreground/90">
                      {skillContent}
                    </pre>
                  ) : (
                    <p className="text-sm text-muted-foreground">暂无内容</p>
                  )}
                </div>
              </div>
            </div>
          </div>

          <DialogFooter className="shrink-0 gap-2 px-6 pb-6 pt-2 border-t">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              关闭
            </Button>
            <Button
              variant="destructive"
              onClick={() => setDeleteConfirmOpen(true)}
              className="gap-1.5"
            >
              <Trash2 className="size-4" />
              删除
            </Button>
          </DialogFooter>
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
