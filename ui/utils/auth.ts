/**
 * fetch with the API key a server running MNEMOS_AUTH_ENABLED=true expects.
 *
 * The key is entered on the sign-in page and kept in storage. A 401 means it
 * was revoked or rotated: it is dropped and the caller is sent back to sign in.
 */

export const API_KEY_STORAGE = 'mnemos.apiKey'

export interface AuthDeps {
  fetch: (input: string, init?: RequestInit) => Promise<Response>
  storage: Pick<Storage, 'getItem' | 'removeItem'>
  onUnauthorized: () => void
}

export type AuthedFetch = (path: string, init?: RequestInit) => Promise<Response>

export function withKey(init: RequestInit | undefined, key: string | null): RequestInit {
  const headers = new Headers(init?.headers)
  if (key) headers.set('authorization', `Bearer ${key}`)
  return { ...init, headers }
}

export function createAuthedFetch(deps: AuthDeps): AuthedFetch {
  return async function authedFetch(path: string, init?: RequestInit): Promise<Response> {
    const res = await deps.fetch(path, withKey(init, deps.storage.getItem(API_KEY_STORAGE)))
    if (res.status === 401) {
      deps.storage.removeItem(API_KEY_STORAGE)
      deps.onUnauthorized()
    }
    return res
  }
}

export type KeyCheck = 'ok' | 'invalid' | 'unreachable'

/** Whether the server accepts `key` (or no key at all, when auth is off). */
export async function checkKey(
  fetch: AuthDeps['fetch'],
  key: string | null,
): Promise<KeyCheck> {
  try {
    const res = await fetch('/api/whoami', withKey(undefined, key))
    if (res.status === 401) return 'invalid'
    return res.ok ? 'ok' : 'unreachable'
  } catch {
    return 'unreachable'
  }
}
