/**
 * Single entry point to the mnemos API.
 *
 * In dev the Nitro proxy forwards /api to :8100; in production the SPA is
 * served by that same server. Either way the browser stays on one origin, so
 * there is nothing to configure and no CORS to arrange.
 */

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

export interface EvalRunSummary {
  tag: string
  created_at?: string
  report: Record<string, any>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'content-type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    throw new Error(`${init?.method ?? 'GET'} ${path} → ${res.status}`)
  }
  return (await res.json()) as T
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

    memories: (status?: string) => {
      const qs = status ? `?status=${encodeURIComponent(status)}` : ''
      return request<{ entries: MemoryEntry[] }>(`/api/memory${qs}`)
    },

    reviewMemory: (id: string, action: 'approve' | 'reject') =>
      request<{ id: string; status: string }>(`/api/memory/${id}/review`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      }),

    evalRuns: () => request<{ runs: EvalRunSummary[] }>('/api/eval/runs'),

    evalRun: (tag: string) =>
      request<Record<string, any>>(`/api/eval/runs?tag=${encodeURIComponent(tag)}`),
  }
}
