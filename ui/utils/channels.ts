/**
 * The channels a file or a change reaches the server through, as declared by
 * the sender's X-Mnemos-Client header.
 */
import { ago } from './connectors'

export const CHANNELS: { value: string; label: string }[] = [
  { value: 'watcher', label: 'Watcher' },
  { value: 'hook', label: 'Git hooks' },
  { value: 'connector', label: 'Connectors' },
  { value: 'cli', label: 'CLI' },
  { value: 'dashboard', label: 'Dashboard' },
  { value: 'mcp', label: 'MCP' },
  { value: 'api', label: 'API' },
  { value: 'script', label: 'Scripts' },
]

const LABELS = Object.fromEntries(CHANNELS.map((c) => [c.value, c.label]))

export function channelLabel(channel: string): string {
  return LABELS[channel] ?? channel
}

export const STALE_AFTER_MS = 24 * 3600 * 1000

/** A channel's last activity: none within the retention, older than a day, or recent. */
export function channelActivity(
  lastAt: string | null | undefined,
  now: number,
  retentionDays: number,
): { text: string; tone: 'never' | 'stale' | 'recent' } {
  if (!lastAt) return { text: `no activity in the last ${retentionDays} days`, tone: 'never' }
  return { text: ago(lastAt, now), tone: now - Date.parse(lastAt) > STALE_AFTER_MS ? 'stale' : 'recent' }
}
