interface AuthBackgroundProps {
  variant: 'brand' | 'form'
}

const grainSvg =
  "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")"

export function AuthBackground({ variant }: AuthBackgroundProps) {
  const isBrand = variant === 'brand'

  return (
    <>
      {/* L0 — 基底渐变 */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background: isBrand
            ? 'radial-gradient(ellipse 100% 80% at 50% 50%, oklch(0.35 0.06 257) 0%, oklch(0.25 0.04 257) 60%, oklch(0.18 0.02 257) 100%)'
            : 'radial-gradient(ellipse 100% 80% at 50% 25%, var(--background) 0%, color-mix(in oklch, var(--background) 88%, var(--muted)) 100%)',
        }}
      />

      {/* L1 — Mesh conic 缓慢旋转色相流动 */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          className="auth-mesh-pan absolute left-1/2 top-1/2 size-[150%] -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{
            opacity: isBrand ? 0.30 : 0.12,
            background: isBrand
              ? 'conic-gradient(from 0deg at 50% 50%, transparent 0deg, oklch(0.588 0.166 257 / 0.25) 60deg, transparent 120deg, oklch(0.70 0.11 70 / 0.15) 180deg, transparent 240deg, oklch(0.588 0.166 257 / 0.2) 300deg, transparent 360deg)'
              : 'conic-gradient(from 0deg at 50% 50%, transparent 0deg, color-mix(in oklch, var(--primary) 8%, transparent) 60deg, transparent 120deg, color-mix(in oklch, var(--warning) 6%, transparent) 180deg, transparent 240deg, color-mix(in oklch, var(--primary) 6%, transparent) 300deg, transparent 360deg)',
            filter: 'blur(60px)',
          }}
        />
      </div>

      {/* L2 — Aurora 光斑（3 个大型柔光漂移） */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          className="auth-aurora-drift absolute rounded-full"
          style={{
            top: '5%',
            left: isBrand ? '10%' : '5%',
            width: '40%',
            height: '50%',
            background: isBrand
              ? 'radial-gradient(circle, oklch(0.70 0.11 70 / 0.18) 0%, transparent 70%)'
              : 'radial-gradient(circle, color-mix(in oklch, var(--warning) 10%, transparent) 0%, transparent 70%)',
            filter: 'blur(40px)',
          }}
        />
        <div
          className="auth-aurora-drift-rev absolute rounded-full"
          style={{
            bottom: '5%',
            right: isBrand ? '5%' : '10%',
            width: '35%',
            height: '45%',
            background: isBrand
              ? 'radial-gradient(circle, oklch(0.588 0.166 257 / 0.22) 0%, transparent 70%)'
              : 'radial-gradient(circle, color-mix(in oklch, var(--primary) 8%, transparent) 0%, transparent 70%)',
            filter: 'blur(50px)',
          }}
        />
        <div
          className="auth-aurora-drift absolute rounded-full"
          style={{
            top: '40%',
            right: isBrand ? '20%' : '15%',
            width: '25%',
            height: '35%',
            background: isBrand
              ? 'radial-gradient(circle, oklch(0.588 0.166 257 / 0.15) 0%, transparent 70%)'
              : 'radial-gradient(circle, color-mix(in oklch, var(--primary) 5%, transparent) 0%, transparent 70%)',
            filter: 'blur(35px)',
            animationDelay: '5s',
          }}
        />
      </div>

      {/* L3 — 技术感细网格（masked 渐隐） */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: isBrand
            ? 'repeating-linear-gradient(0deg, oklch(0.98 0 0) 0, oklch(0.98 0 0) 1px, transparent 1px, transparent 48px), repeating-linear-gradient(90deg, oklch(0.98 0 0) 0, oklch(0.98 0 0) 1px, transparent 1px, transparent 48px)'
            : 'repeating-linear-gradient(0deg, var(--foreground) 0, var(--foreground) 1px, transparent 1px, transparent 48px), repeating-linear-gradient(90deg, var(--foreground) 0, var(--foreground) 1px, transparent 1px, transparent 48px)',
          opacity: isBrand ? 0.04 : 0.025,
          maskImage:
            'radial-gradient(ellipse 80% 80% at 50% 50%, black 30%, transparent 80%)',
          WebkitMaskImage:
            'radial-gradient(ellipse 80% 80% at 50% 50%, black 30%, transparent 80%)',
        }}
      />

      {/* L4 — 噪点纹理（消除 banding + 胶片质感） */}
      <div
        className="pointer-events-none absolute inset-0 mix-blend-overlay"
        style={{
          backgroundImage: grainSvg,
          opacity: isBrand ? 0.06 : 0.035,
        }}
      />

      {/* L5 — 暗角（边缘加深增加深度） */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background: isBrand
            ? 'radial-gradient(ellipse 100% 100% at 50% 50%, transparent 50%, oklch(0.10 0.004 280 / 0.5) 100%)'
            : 'radial-gradient(ellipse 100% 100% at 50% 50%, transparent 60%, color-mix(in oklch, var(--muted) 40%, transparent) 100%)',
        }}
      />
    </>
  )
}
