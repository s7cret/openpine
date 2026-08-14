import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  API_TOKEN_STORAGE_KEY,
  applyApiAuth,
  authenticatedFetch,
  clearApiToken,
  getApiToken,
  getApiWebSocketProtocols,
  setApiToken,
  subscribeUnauthorized,
} from './auth'

class MemoryStorage {
  private values = new Map<string, string>()
  getItem(key: string) { return this.values.get(key) ?? null }
  setItem(key: string, value: string) { this.values.set(key, value) }
  removeItem(key: string) { this.values.delete(key) }
  clear() { this.values.clear() }
}

describe('central API authentication', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    vi.stubGlobal('sessionStorage', new MemoryStorage())
  })

  it('stores only the user-supplied bearer token for the browser session', () => {
    setApiToken('  secret-from-user  ')
    expect(sessionStorage.getItem(API_TOKEN_STORAGE_KEY)).toBe('secret-from-user')
    expect(getApiToken()).toBe('secret-from-user')

    const config = applyApiAuth({ headers: {} })
    expect((config.headers as Record<string, string>).Authorization).toBe('Bearer secret-from-user')

    clearApiToken()
    expect(getApiToken()).toBe('')
  })

  it('encodes WebSocket auth in a subprotocol instead of the request URL', () => {
    setApiToken('session-secret')
    expect(getApiWebSocketProtocols()).toEqual([
      'openpine.bearer.b64.c2Vzc2lvbi1zZWNyZXQ',
      'openpine.events.v1',
    ])
    clearApiToken()
    expect(getApiWebSocketProtocols()).toEqual([])
  })

  it('adds auth to fetch without putting the token in the URL and rejects non-ok responses', async () => {
    setApiToken('session-secret')
    const response = { ok: false, status: 403, statusText: 'Forbidden' } as Response
    const fetchMock = vi.fn().mockResolvedValue(response)
    vi.stubGlobal('fetch', fetchMock)

    await expect(authenticatedFetch('/api/report?format=zip')).rejects.toThrow('403')
    expect(fetchMock).toHaveBeenCalledWith('/api/report?format=zip', expect.objectContaining({
      headers: expect.any(Headers),
    }))
    const headers = fetchMock.mock.calls[0][1].headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer session-secret')
    expect(fetchMock.mock.calls[0][0]).not.toContain('session-secret')
  })

  it('never forwards the operator token to an absolute cross-origin download URL', async () => {
    setApiToken('session-secret')
    vi.stubGlobal('location', new URL('https://openpine.local/dashboard'))
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 } as Response)
    vi.stubGlobal('fetch', fetchMock)

    await authenticatedFetch('https://objects.example/artifact.zip')

    const headers = fetchMock.mock.calls[0][1].headers as Headers
    expect(headers.get('Authorization')).toBeNull()
  })

  it('notifies the UI to show the unlock prompt on 401', async () => {
    const listener = vi.fn()
    const unsubscribe = subscribeUnauthorized(listener)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, statusText: 'Unauthorized' }))

    await expect(authenticatedFetch('/api/private')).rejects.toThrow('401')
    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
  })

  it('replays a 401 that arrives before the layout subscribes', async () => {
    const listener = vi.fn()
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401, statusText: 'Unauthorized' })
    vi.stubGlobal('fetch', fetchMock)

    await expect(authenticatedFetch('/api/private')).rejects.toThrow('401')
    const unsubscribe = subscribeUnauthorized(listener)
    await Promise.resolve()

    expect(listener).toHaveBeenCalledTimes(1)
    unsubscribe()
  })
})
