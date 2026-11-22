import { describe, expect, it, vi } from 'vitest'
import { createAuthedFetch } from './auth'

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    data,
  }
}

const authHeader = (init?: RequestInit) =>
  new Headers(init?.headers).get('authorization')

describe('createAuthedFetch', () => {
  it('sends the stored key as a bearer header', async () => {
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response('{}', { status: 200 }))
    const authed = createAuthedFetch({ fetch, storage: memoryStorage({ 'mnemos.apiKey': 'k1' }), ask: vi.fn() })

    await authed('/api/status')

    expect(authHeader(fetch.mock.calls[0]?.[1])).toBe('Bearer k1')
  })

  it('asks for a key on 401, stores it and retries once', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))
    const storage = memoryStorage()
    const ask = vi.fn(async () => ' k2 ')
    const authed = createAuthedFetch({ fetch, storage, ask })

    const res = await authed('/api/status')

    expect(res.status).toBe(200)
    expect(ask).toHaveBeenCalledTimes(1)
    expect(storage.data.get('mnemos.apiKey')).toBe('k2')
    expect(authHeader(fetch.mock.calls[1]?.[1])).toBe('Bearer k2')
  })

  it('asks only once when several requests hit 401 together', async () => {
    let authorized = false
    const fetch = vi.fn(async () => new Response('{}', { status: authorized ? 200 : 401 }))
    const ask = vi.fn(async () => {
      authorized = true
      return 'k3'
    })
    const authed = createAuthedFetch({ fetch, storage: memoryStorage(), ask })

    const results = await Promise.all([authed('/api/a'), authed('/api/b')])

    expect(ask).toHaveBeenCalledTimes(1)
    expect(results.map(r => r.status)).toEqual([200, 200])
  })

  it('gives up with the 401 when the user cancels', async () => {
    const fetch = vi.fn(async () => new Response('', { status: 401 }))
    const authed = createAuthedFetch({ fetch, storage: memoryStorage(), ask: async () => null })

    const res = await authed('/api/status')

    expect(res.status).toBe(401)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
