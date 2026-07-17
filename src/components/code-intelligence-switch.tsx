'use client'

import { getCodeIntelligenceSwitchVisual } from '@/lib/code-intelligence'
import { cn } from '@/lib/utils'

export function CodeIntelligenceSwitch({
  checked,
  disabled = false,
  label,
  onClick,
}: {
  checked: boolean
  disabled?: boolean
  label: string
  onClick: () => void
}) {
  const visual = getCodeIntelligenceSwitchVisual(checked)

  return (
    <button
      type="button"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'relative h-5 w-9 shrink-0 overflow-hidden rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60',
        visual.track,
      )}
    >
      <span className={visual.thumb} />
    </button>
  )
}
