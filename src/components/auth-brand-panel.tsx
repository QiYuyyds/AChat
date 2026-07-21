import { AgentChatPreview } from '@/components/agent-chat-preview'
import { AuthBackground } from '@/components/auth-background'
import { AuthLogo } from '@/components/AuthLogo'

export function AuthBrandPanel() {
  return (
    <div className="relative hidden flex-col overflow-hidden p-10 lg:flex lg:w-3/5 xl:p-12">
      {/* 高级多层背景 */}
      <AuthBackground variant="brand" />

      {/* 顶部品牌标识 */}
      <div className="auth-fade-up relative z-10 flex shrink-0 items-center gap-3 text-primary-foreground">
        <AuthLogo size={36} variant="brand" />
        <span className="text-lg font-semibold tracking-tight">AChat</span>
      </div>

      {/* 中部 — 标题 + 活体群聊预览，flex-1 撑满 */}
      <div className="relative z-10 flex min-h-0 flex-1 flex-col gap-6 py-8">
        <div className="auth-fade-up-delay-1 shrink-0">
          <h1 className="auth-shimmer-text text-4xl font-bold leading-[1.05] tracking-tight md:text-5xl">
            多个 Agent，
            <br />
            一个群聊
          </h1>
          <p className="auth-fade-up-delay-2 mt-3 max-w-md text-[15px] leading-relaxed text-primary-foreground/55">
            Orchestrator 自动拆任务、并行调度、聚合产物。你只管发需求。
          </p>
        </div>
        <div className="auth-fade-up-delay-3 min-h-0 flex-1">
          <AgentChatPreview />
        </div>
      </div>

    </div>
  )
}
