/**
 * Playground requests: from the form to the endpoint body, and back out as a
 * curl or MCP call that replays the same search outside the dashboard.
 */
import type { SearchTarget } from '~/composables/useApi'

export interface PlaygroundForm {
  target: SearchTarget
  query: string
  limit: number
  mode: 'preview' | 'full'
  trace: boolean
  bypassCache: boolean
  collections: string[]
  tagsAny: string
  tagsAll: string
  fileTypes: string
  pathFilter: string
  language: string
  symbolType: string
  memoryType: string
}

export const TARGETS: Array<{ id: SearchTarget; label: string; tool: string }> = [
  { id: 'search', label: 'Everything (search)', tool: 'mnemos_search' },
  { id: 'search-code', label: 'Code', tool: 'mnemos_search_code' },
  { id: 'search-skills', label: 'Skills', tool: 'mnemos_search_skills' },
  { id: 'search-memory', label: 'Memories', tool: 'mnemos_search_memory' },
]

/** Which filters each endpoint accepts; the others are not sent. */
const FILTERS: Record<SearchTarget, readonly string[]> = {
  'search': ['collections', 'tags_any', 'tags_all', 'file_types', 'path_filter', 'mode'],
  'search-code': ['tags_any', 'tags_all', 'language', 'symbol_type', 'path_filter', 'mode'],
  'search-skills': [],
  'search-memory': ['tags_any', 'tags_all', 'memory_type', 'mode'],
}

/** Fields that only describe the Playground call, not the search itself. */
const DIAGNOSTIC = ['trace', 'source', 'use_cache'] as const

/** A comma-separated filter field as its values (spaces stay: a path or tag may hold one). */
export const splitFilterList = (text: string) => text.split(',').map((s) => s.trim()).filter(Boolean)

export function buildBody(form: PlaygroundForm): Record<string, unknown> {
  const candidates: Record<string, unknown> = {
    collections: form.collections.length ? form.collections : undefined,
    tags_any: splitFilterList(form.tagsAny),
    tags_all: splitFilterList(form.tagsAll),
    file_types: splitFilterList(form.fileTypes),
    path_filter: form.pathFilter.trim(),
    language: form.language.trim(),
    symbol_type: form.symbolType.trim(),
    memory_type: form.memoryType.trim(),
    mode: form.mode,
  }
  const allowed = FILTERS[form.target]
  const body: Record<string, unknown> = { query: form.query.trim(), limit: form.limit }
  for (const [key, value] of Object.entries(candidates)) {
    const empty = value === undefined || value === '' || (Array.isArray(value) && !value.length)
    if (allowed.includes(key) && !empty) body[key] = value
  }
  body.trace = form.trace
  body.source = 'playground'
  // Only /api/search has a cache to skip.
  if (form.target === 'search') body.use_cache = !form.bypassCache
  return body
}

/** The search as a real client would send it: no Playground-only fields. */
export function replayBody(body: Record<string, unknown>): Record<string, unknown> {
  const out = { ...body }
  for (const key of DIAGNOSTIC) delete out[key]
  return out
}

export function toCurl(origin: string, target: SearchTarget, body: Record<string, unknown>, withKey: boolean): string {
  // Single-quoted for the shell: a quote inside the JSON closes, escapes, reopens.
  const json = JSON.stringify(replayBody(body)).replaceAll("'", `'\\''`)
  const auth = withKey ? ` \\\n  -H "authorization: Bearer $MNEMOS_API_KEY"` : ''
  return `curl -s -X POST ${origin}/api/${target} \\\n  -H 'content-type: application/json'${auth} \\\n  -d '${json}'`
}

export function toMcpCall(target: SearchTarget, body: Record<string, unknown>): string {
  const tool = TARGETS.find((t) => t.id === target)!.tool
  return `${tool}(${JSON.stringify(replayBody(body), null, 2)})`
}

/** Why a 422 refused the search: FastAPI lists `{ loc, msg }` per field, a handler may send a sentence. */
export function validationMessage(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return null
  const parts = detail.flatMap((d: { loc?: unknown; msg?: unknown } | null) => {
    if (typeof d?.msg !== 'string') return []
    const field = Array.isArray(d.loc) ? d.loc.filter((l) => l !== 'body').join('.') : ''
    return [field ? `${field}: ${d.msg}` : d.msg]
  })
  return parts.length ? parts.join('; ') : null
}
