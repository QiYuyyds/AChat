import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSearchStore } from './search-store'

// Mock the API
vi.mock('@/lib/api', () => ({
  searchMessagesApi: vi.fn(async (q: string) => ({
    hits: [
      {
        messageId: 'm1', conversationId: 'c1', conversationTitle: 'C1',
        role: 'user', agentId: null, agentName: null, agentAvatar: null,
        createdAt: 1, snippetHtml: `...${q}...`,
      },
    ],
    total: 1, tookMs: 5,
  })),
}))

describe('useSearchStore', () => {
  beforeEach(() => {
    useSearchStore.setState({
      isOpen: false, query: '', hits: [], total: 0, loading: false, error: null,
      highlightedMessageId: null, pendingJumpConversationId: null,
    })
    vi.clearAllMocks()
  })

  it('openSearch / closeSearch toggles isOpen', () => {
    useSearchStore.getState().openSearch()
    expect(useSearchStore.getState().isOpen).toBe(true)
    useSearchStore.getState().closeSearch()
    expect(useSearchStore.getState().isOpen).toBe(false)
  })

  it('setQuery updates query', () => {
    useSearchStore.getState().setQuery('hello')
    expect(useSearchStore.getState().query).toBe('hello')
  })

  it('runSearch does not fire for query shorter than 2 chars', async () => {
    const { searchMessagesApi } = await import('@/lib/api')
    useSearchStore.getState().setQuery('a')
    // wait for debounce (200ms) to elapse
    await new Promise((r) => setTimeout(r, 250))
    expect(searchMessagesApi).not.toHaveBeenCalled()
    expect(useSearchStore.getState().hits).toEqual([])
  })

  it('runSearch calls API and sets hits', async () => {
    useSearchStore.getState().setQuery('hello')
    await new Promise((r) => setTimeout(r, 250))
    const state = useSearchStore.getState()
    expect(state.hits.length).toBe(1)
    expect(state.total).toBe(1)
  })

  it('runSearch uses fallback=like when query has < 3 Chinese chars', async () => {
    const { searchMessagesApi } = await import('@/lib/api')
    useSearchStore.getState().setQuery('模型')
    await new Promise((r) => setTimeout(r, 250))
    expect(searchMessagesApi).toHaveBeenCalledWith('模型', expect.objectContaining({ fallback: 'like' }))
  })

  it('runSearch does NOT use fallback when query has 3+ Chinese chars', async () => {
    const { searchMessagesApi } = await import('@/lib/api')
    useSearchStore.getState().setQuery('渲染管线')
    await new Promise((r) => setTimeout(r, 250))
    expect(searchMessagesApi).toHaveBeenCalledWith('渲染管线', expect.not.objectContaining({ fallback: 'like' }))
  })

  it('jumpToHit sets highlightedMessageId and closes', () => {
    useSearchStore.getState().openSearch()
    useSearchStore.getState().jumpToHit({
      messageId: 'm1', conversationId: 'c1', conversationTitle: 'C1',
      role: 'user', agentId: null, agentName: null, agentAvatar: null,
      createdAt: 1, snippetHtml: '',
    })
    const s = useSearchStore.getState()
    expect(s.isOpen).toBe(false)
    expect(s.highlightedMessageId).toBe('m1')
    expect(s.pendingJumpConversationId).toBe('c1')
  })

  it('clearHighlight resets the field', () => {
    useSearchStore.setState({ highlightedMessageId: 'm1' })
    useSearchStore.getState().clearHighlight()
    expect(useSearchStore.getState().highlightedMessageId).toBeNull()
  })

  it('consumePendingJump returns and clears the pending conversation id', () => {
    useSearchStore.setState({ pendingJumpConversationId: 'c1' })
    const id = useSearchStore.getState().consumePendingJump()
    expect(id).toBe('c1')
    expect(useSearchStore.getState().pendingJumpConversationId).toBeNull()
  })
})