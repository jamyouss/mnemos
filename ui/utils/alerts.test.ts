import { describe, expect, it } from 'vitest'
import { parseDismissed, pruneDismissed, visibleAlerts } from './alerts'

describe('alert dismissal', () => {
  const alerts = [{ id: 'qdrant-down' }, { id: 'sync-silent' }]

  it('hides dismissed alerts only', () => {
    expect(visibleAlerts(alerts, ['sync-silent'])).toEqual([{ id: 'qdrant-down' }])
  })

  it('forgets a dismissal once its alert clears, so it shows again when it returns', () => {
    let dismissed = ['sync-silent']
    dismissed = pruneDismissed(dismissed, ['qdrant-down'])
    expect(dismissed).toEqual([])
    expect(visibleAlerts(alerts, dismissed)).toHaveLength(2)
  })

  it('keeps the dismissal while the alert is still raised', () => {
    expect(pruneDismissed(['sync-silent'], ['sync-silent', 'qdrant-down'])).toEqual(['sync-silent'])
  })

  it('reads anything unexpected in storage as no dismissal', () => {
    expect(parseDismissed(null)).toEqual([])
    expect(parseDismissed('not json')).toEqual([])
    expect(parseDismissed('{"a":1}')).toEqual([])
    expect(parseDismissed('["a", 2]')).toEqual(['a'])
  })
})
