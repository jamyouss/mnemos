/**
 * fetch with the API key a server running MNEMOS_AUTH_ENABLED=true expects.
 *
 * The key is asked for on the first 401 and kept in storage; requests that hit
 * 401 at the same time share one prompt instead of stacking several.
 */

export const API_KEY_STORAGE = 'mnemos.apiKey'

export interface AuthDeps {
  fetch: (input: string, init?: RequestInit) => Promise<Response>
  storage: Pick<Storage, 'getItem' | 'setItem'>
  ask: () => Promise<string | null> | string | null
}

export type AuthedFetch = (path: string, init?: RequestInit) => Promise<Response>

export function createAuthedFetch(deps: AuthDeps): AuthedFetch {
  let pendingAsk: Promise<string | null> | null = null

  const send = (path: string, init: RequestInit | undefined, key: string | null) => {
    const headers = new Headers(init?.headers)
    if (key) headers.set('authorization', `Bearer ${key}`)
    return deps.fetch(path, { ...init, headers })
  }

  return async function authedFetch(path: string, init?: RequestInit): Promise<Response> {
    const res = await send(path, init, deps.storage.getItem(API_KEY_STORAGE))
    if (res.status !== 401) return res

    pendingAsk ??= Promise.resolve(deps.ask())
      .then((entered) => {
        const key = entered?.trim() || null
        if (key) deps.storage.setItem(API_KEY_STORAGE, key)
        return key
      })
      .finally(() => { pendingAsk = null })
    const key = await pendingAsk
    return key ? send(path, init, key) : res
  }
}
