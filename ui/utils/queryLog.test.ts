import { describe, expect, it } from 'vitest'
import { byDay, byIntent, latencyStats, percentile, rates, scoreBuckets, timeSeries, topFiles } from './queryLog'

const entry = (over: Partial<any> = {}) => ({
  ts: 1_700_000_000,
  intent: 'search_code',
  query: 'q',
  n_results: 5,
  latency_ms: 40,
  ...over,
})

describe('percentile', () => {
  it('returns a value that actually occurred', () => {
    // Nearest-rank, not interpolation: a reported p50 must be a latency the
    // system really produced, not an average of two it never did.
    expect(percentile([10, 20, 30, 40], 50)).toBe(20)
    expect(percentile([10, 20, 30, 40], 95)).toBe(40)
    expect(percentile([5], 50)).toBe(5)
  })

  it('is 0 on an empty series rather than NaN', () => {
    expect(percentile([], 50)).toBe(0)
  })
})

describe('latencyStats', () => {
  it('ignores entries with no usable latency', () => {
    const stats = latencyStats([
      entry({ latency_ms: 10 }),
      entry({ latency_ms: undefined }),
      entry({ latency_ms: 'slow' }),
      entry({ latency_ms: 30 }),
    ] as any)
    expect(stats.count).toBe(2)
    expect(stats.max).toBe(30)
  })

  it('reports zeros on an empty log instead of throwing', () => {
    expect(latencyStats([])).toEqual({ count: 0, p50: 0, p95: 0, max: 0 })
  })
})

describe('byDay / byIntent', () => {
  it('groups by calendar day', () => {
    const day = 24 * 3600
    const counts = byDay([entry(), entry(), entry({ ts: 1_700_000_000 + day })] as any)
    expect(Object.values(counts).sort()).toEqual([1, 2])
  })

  it('counts intents', () => {
    const counts = byIntent([entry(), entry({ intent: 'search' })] as any)
    expect(counts).toEqual({ search_code: 1, search: 1 })
  })
})

describe('topFiles', () => {
  it('ranks by frequency and strips the mount prefix', () => {
    const rows = topFiles([
      entry({ top_files: ['/data/codebase/a/x.go', '/data/codebase/b/y.go'] }),
      entry({ top_files: ['/data/codebase/a/x.go'] }),
    ] as any)
    expect(rows[0]).toEqual({ file: 'a/x.go', count: 2 })
    expect(rows[1]).toEqual({ file: 'b/y.go', count: 1 })
  })

  it('tolerates entries without files', () => {
    expect(topFiles([entry(), entry({ top_files: [] })] as any)).toEqual([])
  })
})

describe('scoreBuckets', () => {
  const tops = (...scores: number[]) => scores.map((s) => entry({ top_scores: [s] }))

  it('buckets on the range the scores span', () => {
    const rows = scoreBuckets(tops(0.55, 0.52, 0.91))
    expect(rows[0]).toEqual({ bucket: '0.50–0.55', count: 1 })
    expect(rows.find((r) => r.bucket === '0.55–0.60')?.count).toBe(1)
    expect(rows.at(-1)).toEqual({ bucket: '0.90–0.95', count: 1 })
  })

  it('keeps scores above 1 in their own buckets instead of piling them into the last', () => {
    // Weighted RRF (dense weight 3.0) puts a normal top-1 above 1.
    const rows = scoreBuckets(tops(0.75, 1.08, 1.25, 1.25))
    expect(rows.find((r) => r.bucket === '1.25–1.30')?.count).toBe(2)
    expect(rows.find((r) => r.bucket === '1.05–1.10')?.count).toBe(1)
    expect(rows.reduce((n, r) => n + r.count, 0)).toBe(4)
  })

  it('keeps empty buckets inside the range, so a gap reads as a gap', () => {
    const rows = scoreBuckets(tops(0.1, 0.9))
    expect(rows.map((r) => r.count)).toEqual([1, 0, 0, 0, 0, 0, 0, 0, 1])
  })

  it('puts a score on a bucket edge in the bucket that starts there', () => {
    const rows = scoreBuckets(tops(0.1, 0.3, 1.1))
    expect(rows.find((r) => r.bucket === '0.3–0.4')?.count).toBe(1)
  })

  it('gives one bucket when every score is equal, none when there is no score', () => {
    expect(scoreBuckets(tops(0.7, 0.7))).toEqual([{ bucket: '0.7–0.8', count: 2 }])
    expect(scoreBuckets([])).toEqual([])
  })
})

describe('timeSeries', () => {
  const hour = 3600
  // Aligned on a local hour boundary so the bucket arithmetic is exact in any TZ.
  const t0 = Math.floor(new Date(2026, 0, 10, 12).getTime() / 1000)

  it('zero-fills quiet hours so a gap reads as a dip, not a straight line', () => {
    const series = timeSeries([entry({ ts: t0 + 60 }), entry({ ts: t0 + 3 * hour })], t0, t0 + 3 * hour, 'hour')
    expect(series.map((b) => b.count)).toEqual([1, 0, 0, 1])
  })

  it('splits each bucket by intent and computes latency per bucket', () => {
    const series = timeSeries(
      [entry({ ts: t0 + 1, intent: 'search', latency_ms: 10 }), entry({ ts: t0 + 2, latency_ms: 90 })],
      t0,
      t0 + 60,
      'hour',
    )
    expect(series).toHaveLength(1)
    expect(series[0]?.byIntent).toEqual({ search: 1, search_code: 1 })
    expect(series[0]?.p95).toBe(90)
  })

  it('drops entries outside the window instead of stretching it', () => {
    const series = timeSeries([entry({ ts: t0 - hour }), entry({ ts: t0 + 10 * hour })], t0, t0 + hour, 'hour')
    expect(series.reduce((n, b) => n + b.count, 0)).toBe(0)
  })

  it('buckets by local calendar day', () => {
    const day = 24 * hour
    const series = timeSeries([entry({ ts: t0 }), entry({ ts: t0 + day })], t0, t0 + day, 'day')
    expect(series.map((b) => b.count)).toEqual([1, 1])
  })
})

describe('rates', () => {
  it('reports cache hits and empty results as shares of all searches', () => {
    const r = rates([entry({ cache_hit: true }), entry({ n_results: 0 }), entry(), entry()])
    expect(r).toEqual({ cacheHit: 0.25, zeroResults: 0.25 })
  })

  it('is 0, not NaN, on an empty log', () => {
    expect(rates([])).toEqual({ cacheHit: 0, zeroResults: 0 })
  })
})
