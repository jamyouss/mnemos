import { describe, expect, it, vi } from 'vitest'
import { checkKey, createAuthedFetch } from './auth'

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (k: string) => data.get(k) ?? null,
    removeItem: (k: string) => void data.delete(k),
    data,
  }
}

const authHeader = (init?: RequestInit) =>
  new Headers(init?.headers).get('authorization')

describe('createAuthedFetch', () => {
  it('sends the stored key as a bearer header', async () => {
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response('{}', { status: 200 }))
    const onUnauthorized = vi.fn()
    const authed = createAuthedFetch({ fetch, storage: memoryStorage({ 'mnemos.apiKey': 'k1' }), onUnauthorized })

    await authed('/api/status')

    expect(authHeader(fetch.mock.calls[0]?.[1])).toBe('Bearer k1')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('drops a key the server rejects and sends the user to sign in', async () => {
    const fetch = vi.fn(async () => new Response('', { status: 401 }))
    const storage = memoryStorage({ 'mnemos.apiKey': 'revoked' })
    const onUnauthorized = vi.fn()
    const authed = createAuthedFetch({ fetch, storage, onUnauthorized })

    const res = await authed('/api/status')

    expect(res.status).toBe(401)
    expect(storage.data.has('mnemos.apiKey')).toBe(false)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('keeps the key on a 403: a valid key without the right scope', async () => {
    const storage = memoryStorage({ 'mnemos.apiKey': 'member' })
    const onUnauthorized = vi.fn()
    const authed = createAuthedFetch({ fetch: async () => new Response('', { status: 403 }), storage, onUnauthorized })

    await authed('/api/tenants')

    expect(storage.data.get('mnemos.apiKey')).toBe('member')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})

describe('checkKey', () => {
  it('tells a valid key, a refused key and an unreachable server apart', async () => {
    expect(await checkKey(async () => new Response('{}', { status: 200 }), 'k')).toBe('ok')
    expect(await checkKey(async () => new Response('', { status: 401 }), 'k')).toBe('invalid')
    expect(await checkKey(async () => new Response('', { status: 502 }), 'k')).toBe('unreachable')
    expect(await checkKey(async () => { throw new TypeError('offline') }, 'k')).toBe('unreachable')
  })
})
