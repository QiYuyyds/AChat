'use client'

import type { LucideIcon } from 'lucide-react'
import { Bot, Check, FileCode, Paperclip, Send, SquareTerminal, User, Wrench, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'

type Role = 'user' | 'orchestrator' | 'agent'

interface ChatChip {
  type: 'tool' | 'artifact' | 'status'
  label: string
}

interface AgentMessage {
  role: Role
  name: string
  accent: string
  Icon: LucideIcon
  content: string
  chips?: ChatChip[]
  dispatch?: boolean
}

const SCRIPT: AgentMessage[] = [
  {
    role: 'user',
    name: '你',
    accent: 'oklch(0.52 0.004 280)',
    Icon: User,
    content: '帮我把认证模块从 session 迁移到 JWT，并补全单元测试',
  },
  {
    role: 'orchestrator',
    name: 'Orchestrator',
    accent: 'oklch(0.588 0.166 257)',
    Icon: Workflow,
    content: '已拆分为 2 个并行子任务',
    dispatch: true,
    chips: [
      { type: 'status', label: '重构 JWT 认证 → Claude' },
      { type: 'status', label: '补全单元测试 → Codex' },
    ],
  },
  {
    role: 'agent',
    name: 'Claude',
    accent: 'oklch(0.58 0.08 270)',
    Icon: Bot,
    content: '分析 auth/session.py，提取现有认证逻辑',
    chips: [{ type: 'tool', label: 'fs_read' }],
  },
  {
    role: 'agent',
    name: 'Codex',
    accent: 'oklch(0.60 0.08 200)',
    Icon: SquareTerminal,
    content: '检索测试覆盖，定位未覆盖分支',
    chips: [{ type: 'tool', label: 'fs_grep' }],
  },
  {
    role: 'agent',
    name: 'Claude',
    accent: 'oklch(0.58 0.08 270)',
    Icon: Bot,
    content: 'JWT 模块已重构，产物已提交',
    chips: [{ type: 'artifact', label: 'auth/jwt.py' }],
  },
  {
    role: 'agent',
    name: 'Codex',
    accent: 'oklch(0.60 0.08 200)',
    Icon: SquareTerminal,
    content: '覆盖率 42% → 87%，测试产物已生成',
    chips: [{ type: 'artifact', label: 'test_auth.py' }],
  },
  {
    role: 'orchestrator',
    name: 'Orchestrator',
    accent: 'oklch(0.588 0.166 257)',
    Icon: Workflow,
    content: '任务完成 · 5 个产物已聚合',
    chips: [{ type: 'status', label: '已完成' }],
  },
]

const HOLD_MS = 3000
const RESET_MS = 700
const USER_STEP_MS = 400
const AGENT_STEP_MS = 1200

type Phase = 'typing' | 'hold' | 'reset'

export function AgentChatPreview() {
  const [step, setStep] = useState(0)
  const [phase, setPhase] = useState<Phase>('typing')

  useEffect(() => {
    if (phase === 'reset') {
      const t = setTimeout(() => {
        setStep(0)
        setPhase('typing')
      }, RESET_MS)
      return () => clearTimeout(t)
    }
    if (phase === 'hold') {
      const t = setTimeout(() => setPhase('reset'), HOLD_MS)
      return () => clearTimeout(t)
    }
    const next = SCRIPT[step]
    if (!next) {
      setPhase('hold')
      return
    }
    const wait = next.role === 'user' ? USER_STEP_MS : AGENT_STEP_MS
    const t = setTimeout(() => {
      setStep((s) => s + 1)
      setPhase('typing')
    }, wait)
    return () => clearTimeout(t)
  }, [phase, step])

  const showTyping =
    phase === 'typing' &&
    step < SCRIPT.length &&
    SCRIPT[step].role !== 'user'
  const fading = phase === 'reset'

  const members = [
    { name: 'Orchestrator', accent: 'oklch(0.588 0.166 257)', Icon: Workflow },
    { name: 'Claude', accent: 'oklch(0.58 0.08 270)', Icon: Bot },
    { name: 'Codex', accent: 'oklch(0.60 0.08 200)', Icon: SquareTerminal },
  ]

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden rounded-2xl border border-primary-foreground/10 bg-primary-foreground/[0.035] backdrop-blur-xl">
      {/* 顶部高光线 */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary-foreground/25 to-transparent" />

      {/* 群头 */}
      <div className="flex shrink-0 items-center justify-between border-b border-primary-foreground/10 px-5 py-3.5">
        <div className="flex items-center gap-3">
          <div className="flex -space-x-2">
            {members.map((m) => (
              <span
                key={m.name}
                className="flex size-7 items-center justify-center rounded-full ring-2 ring-[oklch(0.20_0.004_280)]"
                style={{
                  backgroundColor: `color-mix(in oklch, ${m.accent} 35%, transparent)`,
                }}
              >
                <m.Icon className="size-3.5" style={{ color: m.accent }} />
              </span>
            ))}
          </div>
          <div className="flex flex-col">
            <span className="text-[13px] font-semibold text-primary-foreground/95">
              重构认证模块
            </span>
            <span className="text-[11px] text-primary-foreground/45">
              4 人协作 · 并行执行中
            </span>
          </div>
        </div>
        <span className="flex items-center gap-1.5 rounded-full bg-success/15 px-2.5 py-1 text-[11px] font-medium text-success/90">
          <span className="size-1.5 animate-pulse rounded-full bg-success" />
          LIVE
        </span>
      </div>

      {/* 消息流 — flex-1 撑满中段 */}
      <div
        className={`relative flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-5 py-4 transition-opacity duration-500 ${
          fading ? 'opacity-0' : 'opacity-100'
        }`}
      >
        {SCRIPT.slice(0, step).map((msg, i) => (
          <MessageRow
            key={i}
            msg={msg}
            highlight={phase === 'hold' && i === SCRIPT.length - 1}
          />
        ))}
        {showTyping && <TypingRow msg={SCRIPT[step]} />}

        {/* 底部渐隐遮罩 — 消息流入输入栏时有柔和过渡 */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-primary-foreground/[0.035] to-transparent" />
      </div>

      {/* 底部输入栏 — 让窗口体量完整 */}
      <div className="flex shrink-0 items-center gap-2.5 border-t border-primary-foreground/10 px-4 py-3">
        <button
          type="button"
          className="flex size-8 shrink-0 items-center justify-center rounded-lg text-primary-foreground/40 transition-colors hover:bg-primary-foreground/10 hover:text-primary-foreground/70"
          tabIndex={-1}
        >
          <Paperclip className="size-4" />
        </button>
        <div className="flex h-9 flex-1 items-center rounded-lg border border-primary-foreground/10 bg-primary-foreground/[0.04] px-3 text-[13px] text-primary-foreground/35">
          输入消息，@ 提及 Agent…
        </div>
        <button
          type="button"
          className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary-foreground/15 text-primary-foreground/80 transition-all hover:bg-primary-foreground/25 hover:scale-105 active:scale-95"
          tabIndex={-1}
        >
          <Send className="size-4" />
        </button>
      </div>
    </div>
  )
}

function Avatar({ msg }: { msg: AgentMessage }) {
  return (
    <span
      className="flex size-7 shrink-0 items-center justify-center rounded-full"
      style={{
        backgroundColor: `color-mix(in oklch, ${msg.accent} 30%, transparent)`,
        boxShadow: `inset 0 0 0 1px color-mix(in oklch, ${msg.accent} 40%, transparent)`,
      }}
    >
      <msg.Icon className="size-3.5" style={{ color: msg.accent }} />
    </span>
  )
}

function MessageRow({
  msg,
  highlight = false,
}: {
  msg: AgentMessage
  highlight?: boolean
}) {
  const isUser = msg.role === 'user'
  return (
    <div
      className={`chat-msg-in flex gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'} ${highlight ? 'chat-complete-bounce' : ''}`}
    >
      <Avatar msg={msg} />
      <div
        className={`flex max-w-[80%] flex-col gap-1 ${
          isUser ? 'items-end' : 'items-start'
        }`}
      >
        <span className="px-1 text-[11px] font-medium text-primary-foreground/45">
          {msg.name}
        </span>
        <div
          className={`rounded-xl px-3 py-2 text-[13px] leading-relaxed ${
            isUser
              ? 'bg-primary-foreground/12 text-primary-foreground/90'
              : msg.dispatch
                ? 'border border-warning/25 bg-warning/10 text-primary-foreground/85'
                : 'bg-primary-foreground/[0.05] text-primary-foreground/80'
          }`}
        >
          {msg.content}
        </div>
        {msg.chips && msg.chips.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {msg.chips.map((chip, ci) => (
              <Chip key={ci} chip={chip} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Chip({ chip }: { chip: ChatChip }) {
  const styles: Record<ChatChip['type'], string> = {
    tool: 'bg-primary-foreground/10 text-primary-foreground/60',
    artifact: 'bg-warning/15 text-warning/90',
    status: 'bg-success/15 text-success/85',
  }
  const Icon =
    chip.type === 'tool' ? Wrench : chip.type === 'artifact' ? FileCode : Check
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium ${styles[chip.type]}`}
    >
      <Icon className="size-2.5" />
      {chip.label}
    </span>
  )
}

function TypingRow({ msg }: { msg: AgentMessage }) {
  return (
    <div className="flex gap-2.5">
      <Avatar msg={msg} />
      <div className="flex items-center gap-1 rounded-xl bg-primary-foreground/[0.05] px-3 py-2.5">
        {[0, 180, 360].map((delay) => (
          <span
            key={delay}
            className="chat-typing-dot size-1.5 rounded-full bg-primary-foreground/50"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
    </div>
  )
}
