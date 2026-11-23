import { API_KEY_STORAGE, checkKey, type KeyCheck } from '~/utils/auth'

const browserFetch = (input: string, init?: RequestInit) => fetch(input, init)

// One probe per page load: every navigation awaits it, the server sees one call.
let session: Promise<boolean> | null = null

/** False when the server wants a key this browser does not hold. */
export function sessionReady(): Promise<boolean> {
  session ??= checkKey(browserFetch, localStorage.getItem(API_KEY_STORAGE)).then((r) => r !== 'invalid')
  return session
}

export async function signIn(key: string): Promise<KeyCheck> {
  const result = await checkKey(browserFetch, key)
  if (result === 'ok') {
    localStorage.setItem(API_KEY_STORAGE, key)
    session = Promise.resolve(true)
    clearNuxtData('whoami')
  }
  return result
}

export function signOut() {
  localStorage.removeItem(API_KEY_STORAGE)
  session = null
  clearNuxtData('whoami')
  return navigateTo('/login')
}

/** After a 401 mid-session (key revoked or rotated elsewhere). */
export function sendToSignIn() {
  session = null
  if (window.location.pathname.startsWith('/login')) return
  const redirect = window.location.pathname + window.location.search
  window.location.assign(`/login?redirect=${encodeURIComponent(redirect)}`)
}
