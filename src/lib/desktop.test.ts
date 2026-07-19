import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ENGINE_TOKEN_HEADER,
  attachEngineTokenHeaders,
  executionBaseUrl,
  isExecutionUrl,
} from './desktop'

describe('desktop execution URL routing', () => {
  beforeEach(() => {
    vi.stubGlobal('window', {
      location: { hostname: 'localhost' },
      achatDesktop: {
        isDesktop: true,
        engineBaseUrl: 'http://127.0.0.1:12066',
        engineToken: 'test-engine-token',
        appVersion: '0.1.0-test',
        selectDirectory: async () => null,
        openPath: async () => undefined,
        getEngineStatus: async () => 'ready' as const,
        restartEngine: async () => undefined,
      },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('executionBaseUrl aligns bridge 127.0.0.1 to page localhost', () => {
    expect(executionBaseUrl('')).toBe('http://localhost:12066')
  })

  it('isExecutionUrl is true when request host is localhost and bridge is 127.0.0.1', () => {
    expect(
      isExecutionUrl('http://localhost:12066/api/agents', ''),
    ).toBe(true)
    expect(
      isExecutionUrl('http://127.0.0.1:12066/api/conversations', ''),
    ).toBe(true)
  })

  it('attachEngineTokenHeaders always adds token for desktop engine targets', () => {
    const headers = attachEngineTokenHeaders(
      {},
      'http://localhost:12066/api/agents',
      '',
    )
    expect(headers[ENGINE_TOKEN_HEADER]).toBe('test-engine-token')
  })

  it('attachEngineTokenHeaders attaches when URL omitted (desktop default)', () => {
    const headers = attachEngineTokenHeaders({})
    expect(headers[ENGINE_TOKEN_HEADER]).toBe('test-engine-token')
  })
})
