import type { NextConfig } from 'next'

// 部署预览站点由 Python 后端在 /deployments/{id}/... 提供；前端把同源
// /deployments/* 透明转发到后端（previewPath 解析为 window.location.origin 同源）。
const BACKEND_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

/** Desktop installable UI: pure static files served by local engine (D26). */
const DESKTOP_STATIC = process.env.ACHAT_DESKTOP_STATIC === '1'

const nextConfig: NextConfig = {
  // Electron: standalone server. Desktop Tauri package: static export into resources/ui.
  output: DESKTOP_STATIC ? 'export' : 'standalone',

  // Static export cannot optimize images through the Next image server.
  images: DESKTOP_STATIC ? { unoptimized: true } : undefined,

  // rewrites cannot be used with `output: 'export'`; desktop talks to engineBaseUrl directly.
  ...(DESKTOP_STATIC
    ? {}
    : {
        async rewrites() {
          return [
            { source: '/deployments/:path*', destination: `${BACKEND_URL}/deployments/:path*` },
            { source: '/api/:path*', destination: `${BACKEND_URL}/api/:path*` },
          ]
        },
      }),

  // 不让 webpack bundle native / SDK 依赖；运行时走 require/import，保留 native binding 与子进程能力
  serverExternalPackages: [
    'better-sqlite3',
    '@anthropic-ai/claude-agent-sdk',
    '@openai/codex-sdk',
    '@openai/codex',
    '@modelcontextprotocol/sdk',
    'pptxgenjs',
    'pdf-parse',
  ],

  outputFileTracingIncludes: {
    '/*': [
      'scripts/agenthub-codex-mcp.mjs',
      // pdf-parse loads pdf.worker.mjs at runtime; keep worker assets in standalone/Electron.
      'node_modules/pdf-parse/dist/**/*',
      'node_modules/pdfjs-dist/**/*',
      'node_modules/.pnpm/pdf-parse@*/node_modules/pdf-parse/dist/**/*',
      'node_modules/.pnpm/pdfjs-dist@*/node_modules/pdfjs-dist/**/*',
    ],
  },

  outputFileTracingExcludes: {
    '/*': [
      '.agenthub-data/**',
      '.claude/**',
      '.git/**',
      '.understand-anything/**',
      '*.md',
      '*.txt',
      'components.json',
      'drizzle.config.ts',
      'eslint.config.mjs',
      'apps/**',
      'dist-electron/**',
      'electron/**',
      'next.config.ts',
      'openspec/**',
      'packages/**',
      'pnpm-lock.yaml',
      'pnpm-workspace.yaml',
      'postcss.config.mjs',
      'public/**',
      'release/**',
      'scripts/electron-*.mjs',
      'scripts/run-electron-node.mjs',
      'skills/**',
      'specs/**',
      'src/**',
      'tsconfig*.json',
      'tsconfig.tsbuildinfo',
    ],
  },
}

export default nextConfig
