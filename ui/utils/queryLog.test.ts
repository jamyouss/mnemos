import { describe, expect, it } from 'vitest'
import { byDay, byIntent, latencyStats, percentile, scoreBuckets, topFiles } from './queryLog'

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
  it('buckets the top-1 score', () => {
    const rows = scoreBuckets([
      entry({ top_scores: [0.55] }),
      entry({ top_scores: [0.52] }),
      entry({ top_scores: [0.91] }),
    ] as any)
    expect(rows.find((r) => r.bucket === '0.5–0.6')!.count).toBe(2)
    expect(rows.find((r) => r.bucket === '0.9–1.0')!.count).toBe(1)
  })

  it('keeps a score of exactly 1 inside the last bucket', () => {
    const rows = scoreBuckets([entry({ top_scores: [1] })] as any)
    expect(rows[rows.length - 1].count).toBe(1)
  })

  it('always returns every bucket, so an empty chart still has an axis', () => {
    expect(scoreBuckets([])).toHaveLength(10)
  })
})
