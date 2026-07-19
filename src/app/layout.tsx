import type { Metadata, Viewport } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'

import { AuthGate } from '@/components/auth-gate'
import { GlobalSearch } from '@/components/global-search'
import { StreamProvider } from '@/components/stream-provider'
import { ThemeProvider } from '@/components/theme-provider'

import './globals.css'

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
})

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
})

export const metadata: Metadata = {
  title: 'AChat',
  description: 'AChat',
}

// 键盘弹起时收缩内容区（而非覆盖），配合 h-dvh 让底部输入框始终可见
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  interactiveWidget: 'resizes-content',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="h-dvh overflow-hidden">
        <ThemeProvider>
          {/* StreamProvider must sit outside AuthGate's loading spinner branch,
              otherwise desktop SSE is torn down on every / ↔ /login paint. */}
          <StreamProvider>
            <AuthGate>{children}</AuthGate>
          </StreamProvider>
        </ThemeProvider>
        <GlobalSearch />
      </body>
    </html>
  )
}
