import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from './auth-store'

const testUser = {
  id: 'user-1',
  email: 'admin@local',
  name: 'Admin',
  avatarUrl: null,
}

describe('useAuthStore VIP login', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      config: { allowRegistration: false, vipLoginEnabled: false },
      isLoading: true,
      isAuthenticated: false,
    })
    vi.restoreAllMocks()
  })

  it('loads public auth config when current session is unauthenticated', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(
        Response.json({ allowRegistration: true, vipLoginEnabled: true }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await useAuthStore.getState().initialize()

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      expect.stringContaining('/api/auth/config'),
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(useAuthStore.getState().config).toEqual({
      allowRegistration: true,
      vipLoginEnabled: true,
    })
    expect(useAuthStore.getState().isLoading).toBe(false)
  })

  it('posts only the password and authenticates the returned user', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({
        user: testUser,
        tokens: { access_token: 'vip-access-token' },
        config: { allowRegistration: false, vipLoginEnabled: true },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await useAuthStore.getState().vipLogin('123456')

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/auth/vip-login'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ password: '123456' }),
      }),
    )
    expect(useAuthStore.getState().user).toEqual(testUser)
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
  })
})
