'use client'

import { CheckCheck, HelpCircle, Loader2, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { AgentAvatar } from '@/components/agent-avatar'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { fetchPendingQuestions, submitQuestionAnswers } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { AskUserAnswer, AskUserQuestionItem } from '@/shared/types'
import { useAppStore, usePendingQuestions } from '@/stores/app-store'

/**
 * AskUserQuestionDialog —— ask_user 工具的结构化问答弹窗。
 *
 * Agent 调 ask_user → 后端 register pending → SSE → store → 此组件挂载渲染。
 * 用户点选项 → 提交后端 → resolver 唤醒 agent 收到答案。
 *
 * 一次显示一个 pending question；队列里多个问题依次处理。Mount 时拉一次兜底（HMR / 刷新场景）。
 */
const FREE_OTHER = '__other__'

export function AskUserQuestionDialog({ conversationId }: { conversationId: string }) {
  const pending = usePendingQuestions(conversationId)
  const setPendingQuestionsForConversation = useAppStore(
    (s) => s.setPendingQuestionsForConversation,
  )
  const agents = useAppStore((s) => s.agents)

  useEffect(() => {
    let cancelled = false
    fetchPendingQuestions(conversationId)
      .then((list) => {
        if (!cancelled) setPendingQuestionsForConversation(conversationId, list)
      })
      .catch((err) => {
        console.warn('[AskUserQuestionDialog] fetch failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [conversationId, setPendingQuestionsForConversation])

  const current = pending[0] ?? null
  const agentName = current ? agents[current.agentId]?.name ?? current.agentId : ''

  return (
    <Dialog open={current !== null} onOpenChange={() => { /* 必须答完才能关 */ }}>
      <DialogContent
        showCloseButton={false}
        className="max-w-[min(720px,calc(100vw-2rem))] sm:max-w-[min(720px,calc(100vw-2rem))] gap-0 p-0"
      >
        {current && (
          <AskUserBody
            conversationId={conversationId}
            questionId={current.id}
            agentName={agentName}
            agentId={current.agentId}
            questions={current.questions}
            queueSize={pending.length}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function AskUserBody({
  conversationId,
  questionId,
  agentName,
  agentId,
  questions,
  queueSize,
}: {
  conversationId: string
  questionId: string
  agentName: string
  agentId: string
  questions: AskUserQuestionItem[]
  queueSize: number
}) {
  const agent = useAppStore((s) => s.agents[agentId])
  const [draft, setDraft] = useState<Record<string, AskUserAnswer>>(() =>
    initialDraft(questions),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 切换不同 question id 时重置 draft
  useEffect(() => {
    setDraft(initialDraft(questions))
    setError(null)
  }, [questionId, questions])

  const allAnswered = useMemo(
    () =>
      questions.every((q) => {
        const a = draft[q.question]
        if (!a) return false
        return a.selectedLabels.length > 0 || !!a.freeformNote?.trim()
      }),
    [questions, draft],
  )

  const handleToggleOption = useCallback(
    (q: AskUserQuestionItem, label: string) => {
      setDraft((prev) => {
        const cur = prev[q.question] ?? { selectedLabels: [], freeformNote: '' }
        const exists = cur.selectedLabels.includes(label)
        let nextLabels: string[]
        if (q.multiSelect) {
          nextLabels = exists
            ? cur.selectedLabels.filter((l) => l !== label)
            : [...cur.selectedLabels, label]
        } else {
          nextLabels = exists ? [] : [label]
        }
        return { ...prev, [q.question]: { ...cur, selectedLabels: nextLabels } }
      })
    },
    [],
  )

  const handleOtherChange = useCallback((q: AskUserQuestionItem, text: string) => {
    setDraft((prev) => {
      const cur = prev[q.question] ?? { selectedLabels: [], freeformNote: '' }
      return { ...prev, [q.question]: { ...cur, freeformNote: text } }
    })
  }, [])

  const handleToggleOther = useCallback((q: AskUserQuestionItem) => {
    setDraft((prev) => {
      const cur = prev[q.question] ?? { selectedLabels: [], freeformNote: '' }
      const has = cur.selectedLabels.includes(FREE_OTHER)
      let nextLabels: string[]
      if (q.multiSelect) {
        nextLabels = has
          ? cur.selectedLabels.filter((l) => l !== FREE_OTHER)
          : [...cur.selectedLabels, FREE_OTHER]
      } else {
        nextLabels = has ? [] : [FREE_OTHER]
      }
      return { ...prev, [q.question]: { ...cur, selectedLabels: nextLabels } }
    })
  }, [])

  const handleSubmit = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      // 把 FREE_OTHER 这个 sentinel 换成 "其他"（label），freeformNote 保留
      const final: Record<string, AskUserAnswer> = {}
      for (const q of questions) {
        const a = draft[q.question]
        if (!a) {
          final[q.question] = { selectedLabels: [] }
          continue
        }
        final[q.question] = {
          selectedLabels: a.selectedLabels.map((l) => (l === FREE_OTHER ? '其他' : l)),
          freeformNote: a.freeformNote,
        }
      }
      await submitQuestionAnswers(conversationId, questionId, final)
      // SSE 会把 pending 从 store 移除
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <>
      <DialogHeader className="border-b p-4">
        <DialogTitle className="flex items-center gap-2">
          {agent ? (
            <AgentAvatar agent={agent} size="sm" />
          ) : (
            <HelpCircle className="size-4 text-[#3370FF]" />
          )}
          <span>{agentName} 想确认几件事</span>
          {queueSize > 1 && (
            <span className="ml-auto text-xs font-normal text-muted-foreground">
              还有 {queueSize - 1} 组待答
            </span>
          )}
        </DialogTitle>
        <DialogDescription>
          点选项给出选择；勾选「其他」可填自由文本说明。所有问题答完后提交。
        </DialogDescription>
      </DialogHeader>

      <ScrollArea className="max-h-[60vh]">
        <div className="space-y-4 p-4">
          {questions.map((q, idx) => (
            <QuestionBlock
              key={`${q.question}-${idx}`}
              index={idx + 1}
              question={q}
              answer={draft[q.question]}
              onToggleOption={(label) => handleToggleOption(q, label)}
              onToggleOther={() => handleToggleOther(q)}
              onChangeOther={(text) => handleOtherChange(q, text)}
            />
          ))}
        </div>
      </ScrollArea>

      {error && (
        <div className="border-t bg-destructive/10 px-4 py-2 text-xs text-destructive">{error}</div>
      )}

      <DialogFooter className="m-0 flex items-center justify-between gap-2 border-t bg-muted/30 p-3 sm:flex-row">
        <span className="text-[11px] text-muted-foreground">
          {allAnswered ? '已答完所有问题' : `${questions.filter((q) => (draft[q.question]?.selectedLabels.length ?? 0) > 0 || !!draft[q.question]?.freeformNote?.trim()).length} / ${questions.length} 已答`}
        </span>
        <Button
          size="sm"
          className="bg-[#3370FF] text-white hover:bg-[#2860e5]"
          disabled={!allAnswered || busy}
          onClick={() => void handleSubmit()}
        >
          {busy ? (
            <Loader2 className="mr-1 size-3.5 animate-spin" />
          ) : (
            <CheckCheck className="mr-1 size-3.5" />
          )}
          提交答案
        </Button>
      </DialogFooter>
    </>
  )
}

function QuestionBlock({
  index,
  question,
  answer,
  onToggleOption,
  onToggleOther,
  onChangeOther,
}: {
  index: number
  question: AskUserQuestionItem
  answer: AskUserAnswer | undefined
  onToggleOption: (label: string) => void
  onToggleOther: () => void
  onChangeOther: (text: string) => void
}) {
  const selected = new Set(answer?.selectedLabels ?? [])
  const otherSelected = selected.has(FREE_OTHER)

  return (
    <section className="space-y-2 rounded-lg border bg-card p-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md bg-[#3370FF]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#3370FF]">
          <Sparkles className="size-2.5" />
          {question.header}
        </span>
        {question.multiSelect && (
          <span className="mt-0.5 shrink-0 text-[10px] text-muted-foreground">多选</span>
        )}
      </div>
      <p className="text-sm font-medium">
        Q{index}. {question.question}
      </p>

      <div className="grid gap-1.5">
        {question.options.map((opt) => {
          const isSel = selected.has(opt.label)
          return (
            <button
              key={opt.label}
              type="button"
              onClick={() => onToggleOption(opt.label)}
              className={cn(
                'flex items-start gap-2 rounded-md border px-2.5 py-2 text-left text-xs transition',
                isSel
                  ? 'border-[#3370FF] bg-[#3370FF]/5'
                  : 'border-transparent bg-muted/30 hover:border-foreground/20',
              )}
            >
              <span
                className={cn(
                  'mt-0.5 inline-flex size-3.5 shrink-0 items-center justify-center rounded-full border-2',
                  isSel ? 'border-[#3370FF] bg-[#3370FF]' : 'border-muted-foreground/40',
                )}
              >
                {isSel && <span className="size-1 rounded-full bg-white" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-medium">{opt.label}</div>
                {opt.description && (
                  <div className="mt-0.5 text-[11px] text-muted-foreground">{opt.description}</div>
                )}
                {opt.preview && (
                  <pre className="mt-1 overflow-x-auto rounded bg-black/5 px-2 py-1 font-mono text-[10px] dark:bg-white/5">
                    {opt.preview}
                  </pre>
                )}
              </div>
            </button>
          )
        })}
        {/* 「其他」自由输入选项 */}
        <button
          type="button"
          onClick={onToggleOther}
          className={cn(
            'flex items-start gap-2 rounded-md border px-2.5 py-2 text-left text-xs transition',
            otherSelected
              ? 'border-amber-400 bg-amber-50/60 dark:bg-amber-950/20'
              : 'border-dashed border-muted-foreground/30 hover:border-foreground/30',
          )}
        >
          <span
            className={cn(
              'mt-0.5 inline-flex size-3.5 shrink-0 items-center justify-center rounded-full border-2',
              otherSelected ? 'border-amber-500 bg-amber-500' : 'border-muted-foreground/40',
            )}
          >
            {otherSelected && <span className="size-1 rounded-full bg-white" />}
          </span>
          <div className="min-w-0 flex-1">
            <div className="font-medium">其他（自由填写）</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              选项以外的答案，下方填写说明
            </div>
          </div>
        </button>
        {otherSelected && (
          <textarea
            value={answer?.freeformNote ?? ''}
            onChange={(e) => onChangeOther(e.target.value)}
            placeholder="写点说明，Agent 会基于这段文字继续"
            className="mt-1 min-h-[60px] w-full resize-y rounded-md border bg-background px-2 py-1.5 text-xs outline-none focus:border-foreground/30"
          />
        )}
      </div>
    </section>
  )
}

function initialDraft(questions: AskUserQuestionItem[]): Record<string, AskUserAnswer> {
  const o: Record<string, AskUserAnswer> = {}
  for (const q of questions) o[q.question] = { selectedLabels: [], freeformNote: '' }
  return o
}
