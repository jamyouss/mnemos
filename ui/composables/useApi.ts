/**
 * Single entry point to the mnemos API.
 *
 * In dev the Nitro proxy forwards /api to :8100; in production the SPA is
 * served by that same server. Either way the browser stays on one origin, so
 * there is nothing to configure and no CORS to arrange.
 */

import { createAuthedFetch, type AuthedFetch } from '../utils/auth'
import { sendToSignIn } from './useSession'
import type { ConnectorSource } from '../utils/connectors'
import type { ProjectPath } from '../utils/projects'
import type { Scope, Tenant, Whoami } from '../utils/tenants'

export interface CollectionInfo {
  points_count: number
  vectors_count?: number
  status?: string
  error?: string
}

export interface StatusResponse {
  status: string
  collections: Record<string, CollectionInfo>
}

export interface QueryLogEntry {
  ts: number
  intent: string
  query: string
  n_results: number
  latency_ms: number
  top_files?: string[]
  top_scores?: number[]
  reranker?: boolean
  grader?: boolean
  cache_hit?: boolean
  collections?: string[]
  /** Set on test traffic ("playground"), which the dashboard leaves out by default. */
  source?: string
}

export interface QueryLogResponse {
  enabled: boolean
  count: number
  entries: QueryLogEntry[]
}

export interface MemoryEntry {
  id: string
  content: string
  memory_type: string
  project?: string | null
  tags: string[]
  status: string
  created_at?: string
}

export interface MemoryQuery {
  status?: string
  type?: string
  q?: string
  offset?: number
  limit?: number
}

export interface MemoryPage {
  entries: MemoryEntry[]
  /** Matches across every page, not the length of `entries`. */
  total: number
  offset: number
  limit: number
}

export type ConfigApply = 'live' | 'restart' | 'reindex'
export type ConfigSource = 'default' | 'env' | 'override'

export interface ConfigField {
  key: string
  env: string
  label: string
  description: string
  type: 'bool' | 'int' | 'float' | 'str' | 'enum' | 'secret' | 'path' | 'url'
  options: string[] | null
  min: number | null
  max: number | null
  value: unknown
  default: unknown
  source: ConfigSource
  editable: boolean
  apply: ConfigApply
  is_set: boolean
}

export interface ConfigSection {
  id: string
  label: string
  fields: ConfigField[]
}

export interface ConfigResponse {
  writable: boolean
  /** Bumped by every save; sent back so a save over someone else's is refused (409). */
  revision: number
  pending_restart: string[]
  sections: ConfigSection[]
  applied?: string[]
}

export interface ConnectorsResponse {
  revision: number
  writable: boolean
  interval: string | null
  sources: ConnectorSource[]
  /** What the ingester would reject; token variables are not checked here. */
  problems: string[]
}

export interface ProjectsResponse {
  revision: number
  writable: boolean
  paths: ProjectPath[]
  problems: string[]
}

export interface TenantsResponse {
  revision: number
  writable: boolean
  tenants: Tenant[]
}

/** Every tenant mutation answers with the new revision, so edits chain without a reload. */
export interface TenantResult {
  tenant: Tenant
  revision: number
}

/** A tenant and its API key, which the server returns this once only. */
export interface IssuedKey extends TenantResult {
  api_key: string
}

export interface TenantChange {
  admin?: boolean
  read?: Scope
  write?: Scope
}

export interface MemoryStats {
  total: number
  by_status: Record<string, number>
  by_type: Record<string, number>
  by_day: Record<string, number>
}

export interface FacetResponse {
  collection: string
  field: string
  counts: Array<{ value: string; count: number }>
}

export interface DepHealth {
  ok: boolean
  latency_ms: number | null
  error: string | null
}

export interface HealthDeps {
  qdrant: DepHealth
  llm: DepHealth & { provider: string; model: string; checked_at: string }
}

/** Carries the server's JSON body so a 422 can show which field failed and a 409 the current revision. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: { detail?: unknown; errors?: Record<string, string>; problems?: string[]; revision?: number } | null,
  ) {
    super(message)
  }
}

/** A save refused because someone else saved since the page loaded; other 409s carry no revision. */
export function isRevisionConflict(e: unknown): e is ApiError {
  return e instanceof ApiError && e.status === 409 && typeof e.body?.revision === 'number'
}

let authedFetch: AuthedFetch | null = null

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Built on first use: storage only exists in the browser.
  authedFetch ??= createAuthedFetch({
    fetch: (input, options) => fetch(input, options),
    storage: window.localStorage,
    onUnauthorized: sendToSignIn,
  })
  const res = await authedFetch(path, {
    headers: { 'content-type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? `: ${body.detail}` : ''
    throw new ApiError(`${init?.method ?? 'GET'} ${path} → ${res.status}${detail}`, res.status, body)
  }
  return (await res.json()) as T
}

export type SearchTarget = 'search' | 'search-code' | 'search-skills' | 'search-memory'

export interface TraceStage {
  name: string
  /** null for a step with nothing to time (cache miss, stage off). */
  ms: number | null
  [detail: string]: unknown
}

export interface TraceCandidate {
  collection: string
  file_path: string
  symbol: string | null
  /** 1-based rank on each side; null = outside that side's top 20. */
  dense_rank: number | null
  sparse_rank: number | null
  fused_rank: number
  fused_score: number
  rerank_rank?: number
  rerank_score?: number
  grade?: string
  via?: string | null
  /** Absent when the candidate did not make the final list. */
  final_rank?: number
  final_score?: number
}

export interface SearchTrace {
  total_ms: number
  stages: TraceStage[]
  candidates: TraceCandidate[]
}

export interface SearchResponse {
  results: Array<Record<string, any>>
  trace?: SearchTrace
}

export function useApi() {
  return {
    status: () => request<StatusResponse>('/api/status'),

    queryLog: (opts: { limit?: number; intent?: string } = {}) => {
      const params = new URLSearchParams()
      if (opts.limit) params.set('limit', String(opts.limit))
      if (opts.intent) params.set('intent', opts.intent)
      const qs = params.toString()
      return request<QueryLogResponse>(`/api/query-log${qs ? `?${qs}` : ''}`)
    },

    memories: (query: MemoryQuery = {}) => {
      const params = new URLSearchParams()
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== '') params.set(key, String(value))
      }
      const qs = params.toString()
      return request<MemoryPage>(`/api/memory${qs ? `?${qs}` : ''}`)
    },

    reviewMemory: (id: string, action: 'approve' | 'reject') =>
      request<{ id: string; status: string }>(`/api/memory/${id}/review`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      }),

    memoryStats: () => request<MemoryStats>('/api/memory/stats'),

    facets: (collection: string, field = 'tags', limit = 12) =>
      request<FacetResponse>(
        `/api/index/facets?collection=${encodeURIComponent(collection)}&field=${encodeURIComponent(field)}&limit=${limit}`,
      ),

    healthDeps: () => request<HealthDeps>('/api/health/deps'),

    whoami: () => request<Whoami>('/api/whoami'),

    config: () => request<ConfigResponse>('/api/config'),

    updateConfig: (values: Record<string, unknown>, revision: number) =>
      request<ConfigResponse>('/api/config', { method: 'PATCH', body: JSON.stringify({ values, revision }) }),

    resetConfig: (key: string, revision: number) =>
      request<ConfigResponse>(`/api/config/${encodeURIComponent(key)}?revision=${revision}`, { method: 'DELETE' }),

    connectors: () => request<ConnectorsResponse>('/api/connectors'),

    saveConnectors: (body: { interval: string; sources: ConnectorSource[]; revision: number }) =>
      request<ConnectorsResponse>('/api/connectors', { method: 'PUT', body: JSON.stringify(body) }),

    projects: () => request<ProjectsResponse>('/api/projects'),

    saveProjects: (body: { paths: ProjectPath[]; revision: number }) =>
      request<ProjectsResponse>('/api/projects', { method: 'PUT', body: JSON.stringify(body) }),

    tenants: () => request<TenantsResponse>('/api/tenants'),

    createTenant: (body: { name: string; admin: boolean; read: Scope; write: Scope; revision: number }) =>
      request<IssuedKey>('/api/tenants', { method: 'POST', body: JSON.stringify(body) }),

    updateTenant: (name: string, change: TenantChange, revision: number) =>
      request<TenantResult>(`/api/tenants/${encodeURIComponent(name)}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...change, revision }),
      }),

    rotateTenantKey: (name: string, revision: number) =>
      request<IssuedKey>(`/api/tenants/${encodeURIComponent(name)}/rotate`, {
        method: 'POST',
        body: JSON.stringify({ revision }),
      }),

    deleteTenant: (name: string, revision: number) =>
      request<{ deleted: string; revision: number }>(`/api/tenants/${encodeURIComponent(name)}?revision=${revision}`, {
        method: 'DELETE',
      }),

    /** Any search endpoint, as the Playground calls it. */
    runSearch: (target: SearchTarget, body: Record<string, unknown>) =>
      request<SearchResponse>(`/api/${target}`, { method: 'POST', body: JSON.stringify(body) }),
  }
}
