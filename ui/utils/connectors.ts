/**
 * connectors.yaml as the Connectors page edits it.
 *
 * The form keeps list fields as free text (`ENG, OPS`) so typing a separator
 * never fights the input; `toPayload` turns it back into what the server
 * validates with the ingester's own parser.
 */

export type SourceType = 'confluence' | 'jira' | 'web'
export type Deployment = 'auto' | 'cloud' | 'datacenter'

export const SOURCE_TYPES: SourceType[] = ['confluence', 'jira', 'web']
export const DEPLOYMENTS: Deployment[] = ['auto', 'cloud', 'datacenter']
export const DEFAULT_MAX_PAGES = 200

/** One source as stored in connectors.yaml: known keys only. */
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
  }
}

/** What the server stores: empty optional fields are left out, not sent blank. */
export function toPayload(form: SourceForm): ConnectorSource {
  const out: ConnectorSource = { type: form.type }
  if (form.name.trim()) out.name = form.name.trim()

  if (form.type === 'web') {
    out.urls = splitList(form.urls)
    if (form.max_pages !== null && form.max_pages !== DEFAULT_MAX_PAGES) out.max_pages = form.max_pages
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
 * mnemos_knowledge. Mirrors `scope_key` / `scope_of` in packages/connectors.
 */
export function scopeTags(source: ConnectorSource): string[] {
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
