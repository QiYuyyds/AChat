/**
 * WorkspaceBackground — 工作区氛围背景（7 层）
 *
 * 与 AuthBackground 的区别：auth 页面是「品牌展示」，氛围大胆戏剧化；
 * workspace 是「专注工作」，氛围极淡极慢，你更多是感受到它而非看到它。
 *
 * 层级（全部 pointer-events-none，fixed 定位铺满视口）：
 *   L0 暖色基底径向渐变 — 中心偏暖，模拟环境光
 *   L1 Mesh conic 极慢旋转 — 90s 周期，色相微流动
 *   L2 Aurora A 漂移 — 左上，primary 色调，32s
 *   L3 Aurora B 反向漂移 — 右下，warning 色调，40s
 *   L4 细点阵网格 — masked 渐隐，比线条更柔和
 *   L5 胶片噪点 — 消除 banding + 胶片质感
 *   L6 顶部微光 + 边缘暗角 — 模拟光从上方洒落 + 增加深度
 *
 * 所有色值用 color-mix(in oklch, ...) 绑定 CSS 变量，自动适配明暗主题。
 */

const grainSvg =
  "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")"

export function WorkspaceBackground() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden>
      {/* L0 — 暖色基底：中心偏暖的径向渐变 */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 120% 90% at 50% 30%, color-mix(in oklch, var(--primary) 4%, var(--background)) 0%, var(--background) 70%)',
        }}
      />

      {/* L1 — Mesh conic 极慢旋转色相流动（90s，极低透明度） */}
      <div className="absolute inset-0 overflow-hidden">
        <div
          className="ws-mesh absolute left-1/2 top-1/2 size-[180%] -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{
            background:
              'conic-gradient(from 0deg at 50% 50%, transparent 0deg, color-mix(in oklch, var(--primary) 6%, transparent) 60deg, transparent 120deg, color-mix(in oklch, var(--warning) 5%, transparent) 180deg, transparent 240deg, color-mix(in oklch, var(--primary) 4%, transparent) 300deg, transparent 360deg)',
            filter: 'blur(80px)',
            opacity: 0.5,
          }}
        />
      </div>

      {/* L2 + L3 — Aurora 双光斑漂移（极慢，极淡） */}
      <div className="absolute inset-0 overflow-hidden">
        {/* Aurora A — 左上，primary 色调，32s */}
        <div
          className="ws-aurora-a absolute rounded-full"
          style={{
            top: '-10%',
            left: '-5%',
            width: '50%',
            height: '60%',
            background:
              'radial-gradient(circle, color-mix(in oklch, var(--primary) 7%, transparent) 0%, transparent 70%)',
            filter: 'blur(60px)',
          }}
        />
        {/* Aurora B — 右下，warning 色调，40s 反向 */}
        <div
          className="ws-aurora-b absolute rounded-full"
          style={{
            bottom: '-10%',
            right: '-5%',
            width: '45%',
            height: '55%',
            background:
              'radial-gradient(circle, color-mix(in oklch, var(--warning) 6%, transparent) 0%, transparent 70%)',
            filter: 'blur(70px)',
          }}
        />
      </div>

      {/* L4 — 细点阵网格（比线条柔和，masked 渐隐） */}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            'radial-gradient(circle, var(--foreground) 0.5px, transparent 0.5px)',
          backgroundSize: '28px 28px',
          opacity: 0.018,
          maskImage:
            'radial-gradient(ellipse 90% 90% at 50% 50%, black 20%, transparent 85%)',
          WebkitMaskImage:
            'radial-gradient(ellipse 90% 90% at 50% 50%, black 20%, transparent 85%)',
        }}
      />

      {/* L5 — 胶片噪点（消除 banding + 胶片质感） */}
      <div
        className="absolute inset-0 mix-blend-overlay"
        style={{
          backgroundImage: grainSvg,
          opacity: 0.025,
        }}
      />

      {/* L6 — 顶部微光 + 边缘暗角（合并为一层渐变） */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'linear-gradient(to bottom, color-mix(in oklch, var(--primary) 3%, transparent) 0%, transparent 12%), radial-gradient(ellipse 110% 100% at 50% 50%, transparent 55%, color-mix(in oklch, var(--foreground) 8%, transparent) 100%)',
        }}
      />
    </div>
  )
}
