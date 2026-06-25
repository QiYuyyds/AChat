'use client'

import { Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function ThemeToggle({ className }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  // 避免 SSR / 首屏 hydration mismatch — 拿到主题后再渲染
  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) {
    return (
      <Button size="icon" variant="ghost" className={cn(className)} disabled>
        <Sun className="size-4" />
      </Button>
    )
  }

  const isDark = resolvedTheme === 'dark'

  return (
    <Button
      size="icon"
      variant="ghost"
      className={cn('group', className)}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
      title={isDark ? '切到 Light Mode' : '切到 Dark Mode'}
      aria-label="切换主题"
    >
      {/* 外层 span 承接 hover / press 形变，内层图标承接 swap 入场动画 —— 分两个元素免得 transform 打架 */}
      <span className="inline-flex motion-safe:transition-transform motion-safe:duration-200 motion-safe:ease-out motion-safe:group-hover:rotate-12 motion-safe:group-hover:scale-110 motion-safe:group-active:scale-90">
        {isDark ? (
          <Sun className="size-4 theme-toggle-icon motion-safe:transition-colors motion-safe:duration-200 group-hover:text-orange-500" />
        ) : (
          <Moon className="size-4 theme-toggle-icon motion-safe:transition-colors motion-safe:duration-200 group-hover:text-amber-500" />
        )}
      </span>
    </Button>
  )
}
