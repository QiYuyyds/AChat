import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'

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
  title: 'AgentHub',
  description: '多 Agent 协作平台',
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
      <body className="h-screen overflow-hidden">
        <ThemeProvider>
          <StreamProvider>{children}</StreamProvider>
        </ThemeProvider>
        <GlobalSearch />
      </body>
    </html>
  )
}
