import { describe, expect, it } from 'vitest'
import { ACTIONS, actionLabel, changeLines, changeSummary, entryTitle } from './audit'

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
    expect(actionLabel('tenant.rotate')).toBe('Rotated the key of tenant')
    expect(actionLabel('connectors.sync')).toBe('Requested a sync')
    expect(actionLabel('future.thing')).toBe('future.thing')
  })

  it('words every action as a whole phrase, not one cut before its object', () => {
    for (const label of Object.values(ACTIONS)) expect(label).not.toMatch(/\b(of|from|to)$/)
  })

  it('prefixes refusals, except actions that are rejections themselves', () => {
    expect(entryTitle({ action: 'tenant.delete', outcome: 'denied' })).toBe('Refused: Revoked the tenant')
    expect(entryTitle({ action: 'tenant.delete', outcome: 'ok' })).toBe('Revoked the tenant')
    expect(entryTitle({ action: 'auth.failed', outcome: 'denied' })).toBe('Rejected an API key')
  })
})
