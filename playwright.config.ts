import path from 'node:path'

import { defineConfig, devices } from '@playwright/test'

/** E2E 专用隔离数据目录（AGENTHUB_DATA_DIR），与 global-setup 共用同一路径。 */
export const E2E_DATA_DIR = path.resolve('.agenthub-data-e2e')

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1, // SQLite 单文件 + 全局 SSE，串行避免相互干扰
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:3000/api/platform',
    reuseExistingServer: false,
    timeout: 120_000,
    env: { ...process.env, AGENTHUB_DATA_DIR: E2E_DATA_DIR },
  },
})
