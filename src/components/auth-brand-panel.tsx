import { ParticleBackground } from '@/components/particle-background'

export function AuthBrandPanel() {
  return (
    <div className="relative hidden flex-col justify-between overflow-hidden bg-gradient-to-br from-primary to-primary/85 p-12 lg:flex lg:w-3/5">
      {/* 方块粒子背景 — 鼠标移动时散开 */}
      <ParticleBackground colorVar="--primary-foreground" />

      {/* 暖光晕 overlay — 呼吸动画 */}
      <div
        className="auth-glow-breathe pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 60% 50% at 30% 20%, var(--warning) 15%, transparent 70%)',
        }}
      />

      {/* 浮动光球 — 用 warning 色做暖光点缀 */}
      <div
        className="auth-orb pointer-events-none absolute size-48 rounded-full"
        style={{
          top: '15%',
          right: '10%',
          background: 'radial-gradient(circle, var(--warning) 0%, transparent 70%)',
          opacity: 0.25,
          animationDelay: '0s',
        }}
      />
      <div
        className="auth-orb pointer-events-none absolute size-32 rounded-full"
        style={{
          bottom: '25%',
          left: '8%',
          background: 'radial-gradient(circle, var(--warning) 0%, transparent 70%)',
          opacity: 0.2,
          animationDelay: '2s',
        }}
      />
      <div
        className="auth-orb pointer-events-none absolute size-24 rounded-full"
        style={{
          top: '50%',
          right: '20%',
          background: 'radial-gradient(circle, var(--primary-foreground) 0%, transparent 70%)',
          opacity: 0.1,
          animationDelay: '4s',
        }}
      />

      {/* 顶部品牌标识 */}
      <div className="auth-fade-up relative z-10 flex items-center gap-3 text-primary-foreground">
        <img src="/favicon.ico" alt="AChat" className="size-8" />
        <span className="text-lg font-semibold tracking-tight">AChat</span>
      </div>

      {/* 中部品牌名 + 标语 */}
      <div className="relative z-10 flex flex-col gap-3 text-primary-foreground">
        <h1 className="auth-shimmer-text auth-fade-up-delay-1 text-4xl font-bold leading-tight tracking-tight">
          AChat
        </h1>
        <p className="auth-fade-up-delay-2 text-lg font-medium text-primary-foreground/80">
          多 Agent 协作平台
        </p>
        <p className="auth-fade-up-delay-3 max-w-md text-sm text-primary-foreground/60">
          把多 Agent 协作做成 IM 群聊体验
        </p>
      </div>

      {/* 底部几何装饰线条 */}
      <div
        className="pointer-events-none absolute bottom-0 left-0 right-0 h-32"
        style={{
          backgroundImage:
            'repeating-linear-gradient(135deg, var(--primary-foreground) 0, var(--primary-foreground) 1px, transparent 1px, transparent 12px)',
          opacity: 0.05,
          maskImage: 'linear-gradient(to top, black, transparent)',
          WebkitMaskImage: 'linear-gradient(to top, black, transparent)',
        }}
      />
    </div>
  )
}
