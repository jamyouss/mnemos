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

/** Smallest of 1, 2, 2.5 or 5 × 10ⁿ at least `raw`: a step people read. */
function niceStep(raw: number): number {
  if (!(raw > 0)) return 0.1
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  for (const s of [1, 2, 2.5, 5]) if (s * magnitude >= raw) return s * magnitude
  return 10 * magnitude
}

/**
 * Distribution of the top-1 score over the range the scores actually span.
 *
 * The scale is not fixed: it moves with the fusion weights and with the
 * reranker (weighted RRF puts top-1 above 1, cross-encoder logits go
 * negative). A fixed 0–1 axis piled every such score into its last bucket.
 *
 * The range is the 2nd–98th percentile, not min–max: one stray logit at −8
 * would otherwise stretch the axis until every normal score shares a single
 * bucket. Scores outside it are counted in "< x" / "≥ y" edge buckets, never
 * dropped. Empty buckets inside the range are kept, so a gap reads as a gap.
 * There is no cutoff separating a good result from a bad one — read the shape.
 */
export function scoreBuckets(entries: QueryLogEntry[], target = 10): Array<{ bucket: string; count: number }> {
  const tops = entries
    .map((e) => e.top_scores?.[0])
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
  if (!tops.length) return []

  let lo = percentile(tops, 2)
  let hi = percentile(tops, 98)
  if (lo === hi) [lo, hi] = [Math.min(...tops), Math.max(...tops)]
  const step = niceStep((hi - lo) / target)
  // The epsilon absorbs float error (0.7 / 0.1 is 6.999…), so a score on an
  // edge lands in the bucket that starts there: 0.3 in 0.30–0.35, not 0.25–0.30.
  const start = Math.floor(lo / step + 1e-9) * step
  const index = (v: number) => Math.floor((v - start) / step + 1e-9)
  const n = index(hi) + 1
  const end = start + n * step
  // As many decimals as the step has: 0.25 → "0.75 – 1.00", 0.05 → "0.50 – 0.55".
  const decimals = (String(Number(step.toPrecision(12))).split('.')[1] ?? '').length
  const fmt = (v: number) => v.toFixed(decimals)

  const below = { bucket: `< ${fmt(start)}`, count: 0 }
  const above = { bucket: `≥ ${fmt(end)}`, count: 0 }
  const inRange = Array.from({ length: n }, (_, i) => ({
    bucket: `${fmt(start + i * step)} – ${fmt(start + (i + 1) * step)}`,
    count: 0,
  }))
  for (const score of tops) {
    const i = index(score)
    if (i < 0) below.count += 1
    else if (i >= n) above.count += 1
    else inRange[i]!.count += 1
  }
  return [...(below.count ? [below] : []), ...inRange, ...(above.count ? [above] : [])]
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
