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
