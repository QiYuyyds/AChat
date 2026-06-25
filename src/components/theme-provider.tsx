'use client'

import { ThemeProvider as NextThemesProvider } from 'next-themes'

/**
 * 主题 Provider。基于 next-themes，用 class 切换（.dark 加在 html）。
 * defaultTheme='system' 跟随系统配色，并允许用户在头部 toggle 中手动切换。
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  )
}
