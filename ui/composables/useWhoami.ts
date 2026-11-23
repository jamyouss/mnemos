/**
 * The caller behind the stored API key, fetched once and shared by the sidebar
 * and every page; `refresh()` asks again.
 */
export function useWhoami() {
  const api = useApi()
  return useAsyncData('whoami', () => api.whoami(), {
    dedupe: 'defer',
    getCachedData: (key, nuxtApp, ctx) => (ctx.cause === 'refresh:manual' ? undefined : nuxtApp.payload.data[key]),
  })
}
