import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  alignLoopbackHost,
  isLoopbackHostname,
  sameLoopbackService,
  urlTargetsEngine,
} from './url'

describe('desktop loopback URL helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('recognizes loopback hostnames', () => {
    expect(isLoopbackHostname('localhost')).toBe(true)
    expect(isLoopbackHostname('127.0.0.1')).toBe(true)
    expect(isLoopbackHostname('::1')).toBe(true)
    expect(isLoopbackHostname('example.com')).toBe(false)
  })

  it('treats localhost and 127.0.0.1 with same port as one service', () => {
    expect(
      sameLoopbackService('http://127.0.0.1:12066', 'http://localhost:12066'),
    ).toBe(true)
    expect(
      sameLoopbackService('http://127.0.0.1:12066', 'http://localhost:12067'),
    ).toBe(false)
    expect(
      sameLoopbackService('http://127.0.0.1:12066', 'http://example.com:12066'),
    ).toBe(false)
  })

  it('aligns engine host to page host for loopback only', () => {
    vi.stubGlobal('window', {
      location: { hostname: 'localhost' },
    })
    expect(alignLoopbackHost('http://127.0.0.1:12066/')).toBe(
      'http://localhost:12066',
    )
  })

  it('aligns the other direction when page is 127.0.0.1', () => {
    vi.stubGlobal('window', {
      location: { hostname: '127.0.0.1' },
    })
    expect(alignLoopbackHost('http://localhost:12066')).toBe(
      'http://127.0.0.1:12066',
    )
  })

  it('does not rewrite non-loopback hosts', () => {
    vi.stubGlobal('window', {
      location: { hostname: 'localhost' },
    })
    expect(alignLoopbackHost('https://api.example.com')).toBe(
      'https://api.example.com',
    )
  })

  it('urlTargetsEngine matches across localhost vs 127.0.0.1', () => {
    expect(
      urlTargetsEngine(
        'http://localhost:12066/api/agents',
        'http://127.0.0.1:12066',
      ),
    ).toBe(true)
    expect(
      urlTargetsEngine(
        'http://127.0.0.1:12066/api/stream',
        'http://localhost:12066',
      ),
    ).toBe(true)
    expect(urlTargetsEngine('/api/conversations', 'http://127.0.0.1:12066')).toBe(
      true,
    )
    expect(
      urlTargetsEngine(
        'http://example.com/api/agents',
        'http://127.0.0.1:12066',
      ),
    ).toBe(false)
  })
})
