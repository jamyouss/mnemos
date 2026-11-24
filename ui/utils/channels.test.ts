import { describe, expect, it } from 'vitest'
import { channelActivity, channelLabel } from './channels'

describe('channels', () => {
  const now = Date.parse('2026-11-24T10:00:00Z')

  it('names known channels and keeps unknown ones as sent', () => {
    expect(channelLabel('hook')).toBe('Git hooks')
    expect(channelLabel('cli')).toBe('CLI')
    expect(channelLabel('robot')).toBe('robot')
  })

  it('tells a silent channel from a recent one and from one never seen', () => {
    expect(channelActivity(null, now, 30)).toEqual({ text: 'no activity in the last 30 days', tone: 'never' })
    expect(channelActivity('2026-11-24T09:00:00Z', now, 30)).toEqual({ text: '1 h ago', tone: 'recent' })
    expect(channelActivity('2026-11-23T09:00:00Z', now, 30)).toEqual({ text: '25 h ago', tone: 'stale' })
  })
})
