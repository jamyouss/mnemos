import { describe, expect, it } from 'vitest'
import { actionLabel, changeLines, changeSummary } from './audit'

describe('audit wording', () => {
  it('reads field changes as old → new', () => {
    expect(changeLines({ write: [['acme'], '*'], admin: [false, true] })).toEqual(['write: acme → *', 'admin: false → true'])
  })

  it('reads named entries as added, removed and changed', () => {
    expect(changeLines({
      added: ['new/'],
      removed: ['old/'],
      changed: { 'acme/': { tags: [['acme'], ['acme', 'web']] } },
    })).toEqual(['Added: new/', 'Removed: old/', 'acme/ · tags: acme → acme, web'])
  })

  it('summarises long change sets on one line', () => {
    expect(changeSummary({ a: [1, 2], b: [3, 4], c: [5, 6] })).toBe('a: 1 → 2 (+2 more)')
    expect(changeSummary({})).toBe('')
    expect(changeLines({ collection: 'mnemos_code', path: null })).toEqual(['collection: mnemos_code', 'path: —'])
  })

  it('names known actions and keeps unknown ones as recorded', () => {
    expect(actionLabel('tenant.rotate')).toBe('Rotated key of')
    expect(actionLabel('future.thing')).toBe('future.thing')
  })
})
