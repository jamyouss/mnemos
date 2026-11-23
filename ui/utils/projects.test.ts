import { describe, expect, it } from 'vitest'
import { groupRows, newRow, problemsByRow, toPathPayload, toRowForm, type ProjectPath } from './projects'

describe('groupRows', () => {
  const rows = [
    { prefix: 'Projects/laposte/dtc/', tags: 'laposte-dtc, laposte' },
    { prefix: 'Projects/accor/docs/', tags: 'accor-docs, accor' },
    { prefix: 'Projects/accor/apps/', tags: 'accor-apps, accor, vue3' },
    { prefix: 'scratch/', tags: 'scratch' },
  ]

  it('groups by organisation, sorted, with short prefixes last', () => {
    expect(groupRows(rows, '').map((g) => [g.key, g.rows.map((r) => r.prefix)])).toEqual([
      ['Projects/accor/', ['Projects/accor/apps/', 'Projects/accor/docs/']],
      ['Projects/laposte/', ['Projects/laposte/dtc/']],
      ['', ['scratch/']],
    ])
  })

  it('filters on prefix or tag, case-insensitively', () => {
    expect(groupRows(rows, 'VUE3').flatMap((g) => g.rows.map((r) => r.prefix))).toEqual(['Projects/accor/apps/'])
    expect(groupRows(rows, 'dtc').flatMap((g) => g.rows.map((r) => r.prefix))).toEqual(['Projects/laposte/dtc/'])
  })
})

describe('toPathPayload', () => {
  it('round-trips a stored prefix unchanged', () => {
    // Otherwise opening the page would mark an untouched mapping as dirty.
    const stored: ProjectPath = {
      prefix: 'acme/webshop/', tags: ['webshop', 'acme', 'vue3'], exclude_dirs: ['exports'], exclude_exts: ['.csv', '.min.js'],
    }
    expect(toPathPayload(toRowForm(stored))).toEqual(stored)
  })

  it('keeps the tag order, so the first tag stays primary, and drops repeats', () => {
    const row = { ...newRow(), prefix: ' acme/ ', tags: 'acme, go, , acme' }
    expect(toPathPayload(row)).toEqual({ prefix: 'acme/', tags: ['acme', 'go'], exclude_dirs: [], exclude_exts: [] })
  })

  it('keeps a space inside a tag but splits ignore lists on spaces too', () => {
    const row = { ...newRow(), prefix: 'a/', tags: 'team one', excludeDirs: 'exports fixtures,tmp', excludeExts: '.csv .tsv' }
    expect(toPathPayload(row)).toEqual({ prefix: 'a/', tags: ['team one'], exclude_dirs: ['exports', 'fixtures', 'tmp'], exclude_exts: ['.csv', '.tsv'] })
  })
})

describe('problemsByRow', () => {
  it('attaches `paths[i]:` problems to their row and keeps the rest general', () => {
    const { byRow, general } = problemsByRow([
      'paths[0]: prefix must not start with /',
      'paths[2]: duplicate prefix acme/',
      'paths[2]: no tags',
      'paths: expected a list',
    ])
    expect([...byRow]).toEqual([
      [0, ['prefix must not start with /']],
      [2, ['duplicate prefix acme/', 'no tags']],
    ])
    expect(general).toEqual(['paths: expected a list'])
  })
})
