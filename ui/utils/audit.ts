/**
 * How the Activity page words the audit trail.
 *
 * The server records `<domain>.<verb>` actions with a `changes` object: either
 * `{field: [old, new]}` per field, or `added` / `removed` / `changed` for a
 * set of named entries (projects by prefix, connector sources by name).
 */

export const DOMAINS: { value: string; label: string }[] = [
  { value: 'tenant', label: 'Tenants' },
  { value: 'projects', label: 'Projects' },
  { value: 'config', label: 'Configuration' },
  { value: 'connectors', label: 'Connectors' },
  { value: 'index', label: 'Index' },
  { value: 'memory', label: 'Memories' },
  { value: 'auth', label: 'Sign-in' },
]

// Whole phrases: the table shows the target on the line below.
export const ACTIONS: Record<string, string> = {
  'tenant.create': 'Created the tenant',
  'tenant.update': 'Changed the rights of tenant',
  'tenant.rotate': 'Rotated the key of tenant',
  'tenant.delete': 'Revoked the tenant',
  'projects.update': 'Saved the project mapping',
  'config.update': 'Changed the settings',
  'config.reset': 'Reset the setting to its default',
  'connectors.update': 'Saved the connectors',
  'connectors.sync': 'Requested a sync',
  'index.reindex': 'Started a reindex',
  'index.delete': 'Deleted a file from the index',
  'memory.create': 'Stored a memory',
  'memory.review': 'Reviewed a memory',
  'memory.update': 'Edited a memory',
  'memory.delete': 'Deleted a memory',
  'memory.extract': 'Extracted memories from the commit',
  'auth.failed': 'Rejected an API key',
}

export function actionLabel(action: string): string {
  return ACTIONS[action] ?? action
}

/** The entry's headline. A refusal says so, unless the action already is one. */
export function entryTitle(entry: { action: string; outcome: 'ok' | 'denied' }): string {
  const label = actionLabel(entry.action)
  return entry.outcome === 'denied' && !entry.action.startsWith('auth.') ? `Refused: ${label}` : label
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '[]'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const isPair = (v: unknown): v is [unknown, unknown] => Array.isArray(v) && v.length === 2

/** One readable line per change, for the detail panel. */
export function changeLines(changes: Record<string, unknown>): string[] {
  const lines: string[] = []
  for (const [key, value] of Object.entries(changes ?? {})) {
    if (key === 'added' || key === 'removed') {
      lines.push(`${key === 'added' ? 'Added' : 'Removed'}: ${show(value)}`)
    } else if (key === 'changed' && value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [name, fields] of Object.entries(value as Record<string, Record<string, unknown>>)) {
        for (const [field, pair] of Object.entries(fields)) {
          lines.push(isPair(pair) ? `${name} · ${field}: ${show(pair[0])} → ${show(pair[1])}` : `${name} · ${field}: ${show(pair)}`)
        }
      }
    } else if (isPair(value)) {
      lines.push(`${key}: ${show(value[0])} → ${show(value[1])}`)
    } else {
      lines.push(`${key}: ${show(value)}`)
    }
  }
  return lines
}

/** The table's one-line summary: the first change, and how many more. */
export function changeSummary(changes: Record<string, unknown>): string {
  const lines = changeLines(changes)
  if (!lines.length) return ''
  return lines.length === 1 ? lines[0]! : `${lines[0]} (+${lines.length - 1} more)`
}
