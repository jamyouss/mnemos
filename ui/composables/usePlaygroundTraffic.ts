import type { QueryLogEntry } from '~/composables/useApi'

/**
 * Playground searches are test traffic: counting them would move the very
 * latency and hit-rate figures someone is trying to read. Left out unless
 * asked for; the choice is shared by every screen of the session.
 */
export function usePlaygroundTraffic() {
  const include = useState('include-playground', () => false)
  const keep = (entries: QueryLogEntry[]) => (include.value ? entries : entries.filter((e) => e.source !== 'playground'))
  return { include, keep }
}
