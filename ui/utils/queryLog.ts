/**
 * Aggregations over the query log.
 *
 * Kept out of the components and tested on their own: these are the numbers
 * the dashboard exists to show, and a silently wrong average would defeat the
 * point of building it.
 */
import type { QueryLogEntry } from '~/composables/useApi'

export function percentile(values: number[], p: number): number {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  // Nearest-rank: no interpolation, so a reported p50 is always a latency
  // that actually happened.
  const rank = Math.ceil((p / 100) * sorted.length)
  return sorted[Math.min(Math.max(rank, 1), sorted.length) - 1]
}

export interface LatencyStats {
  count: number
  p50: number
  p95: number
  max: number
}

export function latencyStats(entries: QueryLogEntry[]): LatencyStats {
  const values = entries
    .map((e) => e.latency_ms)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  return {
    count: values.length,
    p50: percentile(values, 50),
    p95: percentile(values, 95),
    max: values.length ? Math.max(...values) : 0,
  }
}

export function countBy<T>(items: T[], key: (item: T) => string | undefined): Record<string, number> {
  const out: Record<string, number> = {}
  for (const item of items) {
    const k = key(item)
    if (!k) continue
    out[k] = (out[k] ?? 0) + 1
  }
  return out
}

export function byDay(entries: QueryLogEntry[]): Record<string, number> {
  return countBy(entries, (e) =>
    typeof e.ts === 'number' ? new Date(e.ts * 1000).toISOString().slice(0, 10) : undefined,
  )
}

export function byIntent(entries: QueryLogEntry[]): Record<string, number> {
  return countBy(entries, (e) => e.intent)
}

/**
 * Files returned most often, path-relative to the codebase mount.
 *
 * This is the view that surfaced `coverage.out` as the single most-returned
 * file across a sample of real searches — a Go coverage profile matching
 * queries it had nothing to do with.
 */
export function topFiles(entries: QueryLogEntry[], limit = 10): Array<{ file: string; count: number }> {
  const counts = countBy(
    entries.flatMap((e) => e.top_files ?? []),
    (f) => (f ? f.replace('/data/codebase/', '') : undefined),
  )
  return Object.entries(counts)
    .map(([file, count]) => ({ file, count }))
    .sort((a, b) => b.count - a.count || a.file.localeCompare(b.file))
    .slice(0, limit)
}

/**
 * Distribution of the top-1 score, bucketed.
 *
 * Hybrid fusion puts a normal top-1 between 0.5 and 0.8, so the shape matters
 * more than any single threshold — there is no absolute cutoff separating a
 * good result from a bad one.
 */
export function scoreBuckets(entries: QueryLogEntry[], buckets = 10): Array<{ bucket: string; count: number }> {
  const tops = entries
    .map((e) => e.top_scores?.[0])
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))

  const out = Array.from({ length: buckets }, (_, i) => ({
    bucket: `${(i / buckets).toFixed(1)}–${((i + 1) / buckets).toFixed(1)}`,
    count: 0,
  }))
  for (const score of tops) {
    const clamped = Math.min(Math.max(score, 0), 0.999999)
    out[Math.floor(clamped * buckets)].count += 1
  }
  return out
}
