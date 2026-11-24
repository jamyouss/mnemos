/**
 * The connector configuration as the Connectors page edits it.
 *
 * The form keeps list fields as free text (`ENG, OPS`) so typing a separator
 * never fights the input; `toPayload` turns it back into what the server
 * validates with the ingester's own parser.
 */

export type SourceType = 'confluence' | 'jira' | 'web' | 'git'
export type Deployment = 'auto' | 'cloud' | 'datacenter'
export type GitCollection = 'mnemos_code' | 'mnemos_knowledge'

export const SOURCE_TYPES: SourceType[] = ['confluence', 'jira', 'web', 'git']
export const DEPLOYMENTS: Deployment[] = ['auto', 'cloud', 'datacenter']
export const GIT_COLLECTIONS: GitCollection[] = ['mnemos_code', 'mnemos_knowledge']
export const DEFAULT_MAX_PAGES = 200
export const DEFAULT_MAX_FILE_KB = 512

/** One source as the server stores it: known keys only. */
export interface ConnectorSource {
  type: SourceType
  name?: string
  tags?: string[]
  url?: string
  user?: string
  token_env?: string
  deployment?: Deployment
  spaces?: string[]
  projects?: string[]
  jql?: string
  urls?: string[]
  max_pages?: number
  branch?: string
  paths?: string[]
  exclude?: string[]
  max_file_kb?: number
  collection?: GitCollection
}

export interface SourceForm {
  /** Client-side identity for v-for; never sent. */
  id: number
  type: SourceType
  name: string
  tags: string
  url: string
  user: string
  token_env: string
  deployment: Deployment
  /** Confluence spaces or Jira projects. */
  keys: string
  jql: string
  urls: string
  max_pages: number | null
  branch: string
  paths: string
  exclude: string
  max_file_kb: number | null
  collection: GitCollection
}

let nextId = 0

export function splitList(text: string): string[] {
  return text.split(/[\s,]+/).filter(Boolean)
}

const joinList = (items: unknown[] | undefined) => (items ?? []).map(String).join(', ')

const DEFAULT_TOKEN_ENV: Record<SourceType, string> = {
  confluence: 'CONFLUENCE_TOKEN',
  jira: 'JIRA_TOKEN',
  web: '',
  git: '',
}

export function newSource(type: SourceType): SourceForm {
  return toForm({ type, token_env: DEFAULT_TOKEN_ENV[type] || undefined })
}

export function toForm(source: ConnectorSource): SourceForm {
  return {
    id: nextId++,
    type: source.type,
    name: source.name ?? '',
    tags: joinList(source.tags),
    url: source.url ?? '',
    user: source.user ?? '',
    token_env: source.token_env ?? '',
    deployment: source.deployment ?? 'auto',
    keys: joinList(source.type === 'jira' ? source.projects : source.spaces),
    jql: source.jql ?? '',
    urls: joinList(source.urls),
    max_pages: source.max_pages ?? DEFAULT_MAX_PAGES,
    branch: source.branch ?? '',
    paths: joinList(source.paths),
    exclude: joinList(source.exclude),
    max_file_kb: source.max_file_kb ?? DEFAULT_MAX_FILE_KB,
    collection: source.collection ?? 'mnemos_code',
  }
}

/** What the server stores: empty optional fields are left out, not sent blank. */
export function toPayload(form: SourceForm): ConnectorSource {
  const out: ConnectorSource = { type: form.type }
  if (form.name.trim()) out.name = form.name.trim()

  if (form.type === 'web') {
    out.urls = splitList(form.urls)
    if (form.max_pages !== null && form.max_pages !== DEFAULT_MAX_PAGES) out.max_pages = form.max_pages
  } else if (form.type === 'git') {
    out.url = form.url.trim()
    if (form.user.trim()) out.user = form.user.trim()
    // Optional: a public repository needs no token.
    if (form.token_env.trim()) out.token_env = form.token_env.trim()
    if (form.branch.trim()) out.branch = form.branch.trim()
    const paths = splitList(form.paths)
    if (paths.length) out.paths = paths
    const exclude = splitList(form.exclude)
    if (exclude.length) out.exclude = exclude
    if (form.max_file_kb !== null && form.max_file_kb !== DEFAULT_MAX_FILE_KB) out.max_file_kb = form.max_file_kb
    if (form.collection !== 'mnemos_code') out.collection = form.collection
  } else {
    out.url = form.url.trim()
    if (form.user.trim()) out.user = form.user.trim()
    out.token_env = form.token_env.trim()
    if (form.deployment !== 'auto') out.deployment = form.deployment
    out[form.type === 'jira' ? 'projects' : 'spaces'] = splitList(form.keys)
    if (form.type === 'jira' && form.jql.trim()) out.jql = form.jql.trim()
  }

  const tags = splitList(form.tags)
  if (tags.length) out.tags = tags
  return out
}

/**
 * The `<source>:<scope>` tags a source syncs under — its documents in
 * mnemos_knowledge (mnemos_code for most git sources). Mirrors `scope_key` /
 * `scope_of` / `repo_scope` in packages/connectors.
 */
export function scopeTags(source: ConnectorSource): string[] {
  if (source.type === 'git') {
    try {
      const url = new URL((source.url ?? '').trim())
      const path = url.pathname.replace(/^\/+|\/+$/g, '').replace(/\.git$/, '').replace(/\/+$/, '')
      if (!['http:', 'https:'].includes(url.protocol) || !path) return []
      return [`git:${url.host}/${path}`]
    } catch {
      return []
    }
  }
  if (source.type === 'web') {
    const hosts = new Set<string>()
    for (const url of source.urls ?? []) {
      try {
        hosts.add(new URL(url).host)
      } catch {
        // Not a URL yet: the server reports it on save.
      }
    }
    return [...hosts].map((host) => `web:${host}`)
  }
  const keys = (source.type === 'jira' ? source.projects : source.spaces) ?? []
  return keys.map((raw) => {
    const key = String(raw).trim()
    const scope = source.type === 'confluence' && key.startsWith('~') ? key : key.toUpperCase()
    return `${source.type}:${scope}`
  })
}

/** The name the server and the sync service know a source by: its own, else its type. */
export function sourceName(source: Pick<ConnectorSource, 'type' | 'name'>): string {
  return source.name?.trim() || source.type
}

interface SyncState {
  requests: { id: number; sources: string[] | null; requested_at: string }[]
  acked_id: number
}

/** Requests the sync service has not picked up yet. */
export function pendingRequests(state: SyncState | null | undefined) {
  return state ? state.requests.filter((r) => r.id > state.acked_id) : []
}

/** Whether a pending request covers `name` (every-source requests cover them all). */
export function syncPending(state: SyncState | null | undefined, name: string): boolean {
  return pendingRequests(state).some((r) => r.sources === null || r.sources.includes(name))
}

/** Whether the oldest pending request has waited longer than `afterMs`: the sync service is likely not running. */
export function syncStalled(state: SyncState | null | undefined, now: number, afterMs = 30_000): boolean {
  const oldest = pendingRequests(state)[0]
  return !!oldest && now - Date.parse(oldest.requested_at) > afterMs
}

/**
 * Whether a sync service is reporting to this server. It reports every minute
 * while it runs, so three silent minutes mean it stopped or cannot reach us.
 */
export function serviceHealth(seenAt: string | null | undefined, now: number, silentAfterMs = 180_000): 'never' | 'alive' | 'silent' {
  if (!seenAt) return 'never'
  return now - Date.parse(seenAt) > silentAfterMs ? 'silent' : 'alive'
}

/** "just now", "4 min ago", "2 h ago", then the date. */
export function ago(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 48 * 3600) return `${Math.floor(seconds / 3600)} h ago`
  return new Date(iso).toLocaleDateString()
}

interface Run {
  state: 'running' | 'ok' | 'failed'
  started_at: string | null
  finished_at: string | null
}

/** One line for a source's last sync: "syncing since 4 min ago", "synced 2 h ago", "failed just now". */
export function runSummary(run: Run | null | undefined, now: number): string {
  if (!run) return ''
  if (run.state === 'running') return run.started_at ? `syncing, started ${ago(run.started_at, now)}` : 'syncing'
  const when = run.finished_at ? ` ${ago(run.finished_at, now)}` : ''
  return run.state === 'ok' ? `synced${when}` : `sync failed${when}`
}
