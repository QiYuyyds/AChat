import type { Metadata, Viewport } from 'next'
import localFont from 'next/font/local'

import { AuthGate } from '@/components/auth-gate'
import { GlobalSearch } from '@/components/global-search'
import { StreamProvider } from '@/components/stream-provider'
import { ThemeProvider } from '@/components/theme-provider'

import './globals.css'

const manrope = localFont({
  src: [
    { path: '../../public/fonts/manrope-latin-400-normal.woff2', weight: '400', style: 'normal' },
    { path: '../../public/fonts/manrope-latin-500-normal.woff2', weight: '500', style: 'normal' },
    { path: '../../public/fonts/manrope-latin-600-normal.woff2', weight: '600', style: 'normal' },
    { path: '../../public/fonts/manrope-latin-700-normal.woff2', weight: '700', style: 'normal' },
  ],
  variable: '--font-manrope',
  display: 'swap',
})

const geistMono = localFont({
  src: [
    { path: '../../public/fonts/geist-mono-latin-400-normal.woff2', weight: '400', style: 'normal' },
  ],
  variable: '--font-geist-mono',
  display: 'swap',
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
      className={`${manrope.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="h-dvh overflow-hidden">
        <ThemeProvider>
          <AuthGate>
            <StreamProvider>{children}</StreamProvider>
          </AuthGate>
        </ThemeProvider>
        <GlobalSearch />
      </body>
    </html>
  )
}
