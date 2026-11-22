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
  return sorted[Math.min(Math.max(rank, 1), sorted.length) - 1] ?? 0
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
    out[Math.floor(clamped * buckets)]!.count += 1
  }
  return out
}

export type Period = '24h' | '7d' | '30d'

export const PERIODS: Record<Period, { seconds: number; bucket: 'hour' | 'day'; label: string }> = {
  '24h': { seconds: 24 * 3600, bucket: 'hour', label: 'Last 24 hours' },
  '7d': { seconds: 7 * 24 * 3600, bucket: 'day', label: 'Last 7 days' },
  '30d': { seconds: 30 * 24 * 3600, bucket: 'day', label: 'Last 30 days' },
}

export function since(entries: QueryLogEntry[], fromTs: number): QueryLogEntry[] {
  return entries.filter((e) => typeof e.ts === 'number' && e.ts >= fromTs)
}

export interface TimeBucket {
  /** Bucket start, epoch milliseconds (local time), what the chart axis plots. */
  t: number
  count: number
  byIntent: Record<string, number>
  p50: number
  p95: number
}

function bucketStart(ms: number, bucket: 'hour' | 'day'): number {
  const d = new Date(ms)
  d.setMinutes(0, 0, 0)
  if (bucket === 'day') d.setHours(0)
  return d.getTime()
}

function nextBucket(ms: number, bucket: 'hour' | 'day'): number {
  const d = new Date(ms)
  if (bucket === 'hour') d.setHours(d.getHours() + 1)
  else d.setDate(d.getDate() + 1)
  return d.getTime()
}

/**
 * Zero-filled buckets from `fromTs` to `toTs` (epoch seconds).
 *
 * Empty buckets are kept: a gap in traffic has to read as a dip to zero, not
 * as a straight line drawn between the two busy hours around it.
 */
export function timeSeries(
  entries: QueryLogEntry[],
  fromTs: number,
  toTs: number,
  bucket: 'hour' | 'day',
): TimeBucket[] {
  const buckets = new Map<number, { entries: QueryLogEntry[] }>()
  for (let t = bucketStart(fromTs * 1000, bucket); t <= toTs * 1000; t = nextBucket(t, bucket)) {
    buckets.set(t, { entries: [] })
  }
  for (const e of entries) {
    if (typeof e.ts !== 'number' || e.ts < fromTs || e.ts > toTs) continue
    buckets.get(bucketStart(e.ts * 1000, bucket))?.entries.push(e)
  }
  return [...buckets.entries()].map(([t, { entries: items }]) => {
    const stats = latencyStats(items)
    return { t, count: items.length, byIntent: byIntent(items), p50: stats.p50, p95: stats.p95 }
  })
}

export interface Rates {
  /** Share of searches answered from the semantic cache, 0..1. */
  cacheHit: number
  /** Share of searches that returned nothing, 0..1. */
  zeroResults: number
}

export function rates(entries: QueryLogEntry[]): Rates {
  if (!entries.length) return { cacheHit: 0, zeroResults: 0 }
  const hits = entries.filter((e) => e.cache_hit).length
  const empty = entries.filter((e) => e.n_results === 0).length
  return { cacheHit: hits / entries.length, zeroResults: empty / entries.length }
}
