import { cn } from '@/lib/utils'

interface AuthLogoProps {
  size?: number
  className?: string
  variant?: 'surface' | 'brand'
}

export function AuthLogo({ size = 56, className, variant = 'surface' }: AuthLogoProps) {
  const ringClass =
    variant === 'brand'
      ? 'ring-1 ring-primary-foreground/20'
      : 'ring-1 ring-border bg-card'

  return (
    <div
      className={cn(
        'flex items-center justify-center rounded-2xl',
        ringClass,
        className,
      )}
      style={{
        width: size,
        height: size,
        boxShadow: 'var(--shadow-md), var(--inset-hi)',
      }}
    >
      <img
        src="/favicon.ico"
        alt="AChat"
        className="size-1/2"
        style={{ width: size * 0.55, height: size * 0.55 }}
      />
    </div>
  )
}
