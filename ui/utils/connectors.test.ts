import { describe, expect, it } from 'vitest'
import { newSource, scopeTags, sourceName, syncPending, syncStalled, toForm, toPayload, type ConnectorSource } from './connectors'

describe('toPayload', () => {
  it('round-trips a stored source unchanged', () => {
    // Otherwise opening the page would mark an untouched file as dirty.
    const stored: ConnectorSource = {
      type: 'jira', name: 'tracker', url: 'https://jira.example.com', user: 'bot@example.com',
      token_env: 'JIRA_TOKEN', deployment: 'cloud', projects: ['OPS', 'DEV'], jql: 'resolution = Unresolved', tags: ['acme'],
    }
    expect(toPayload(toForm(stored))).toEqual(stored)
  })

  it('splits lists on commas and whitespace', () => {
    const form = { ...newSource('confluence'), url: 'https://wiki.example.com', keys: 'ENG,OPS  ~jdoe,', tags: 'a b' }
    expect(toPayload(form)).toMatchObject({ spaces: ['ENG', 'OPS', '~jdoe'], tags: ['a', 'b'] })
  })

  it('leaves blank optional fields out instead of sending them empty', () => {
    // `user: ""` would switch the ingester to HTTP Basic with an empty login.
    const payload = toPayload({ ...newSource('jira'), url: 'https://jira.example.com', keys: 'OPS' })
    expect(payload).toEqual({ type: 'jira', url: 'https://jira.example.com', token_env: 'JIRA_TOKEN', projects: ['OPS'] })
  })

  it('keeps only the keys of the source type', () => {
    const form = { ...newSource('web'), urls: 'https://docs.example.com/', user: 'leftover', keys: 'ENG', max_pages: 50 }
    expect(toPayload(form)).toEqual({ type: 'web', urls: ['https://docs.example.com/'], max_pages: 50 })
  })
})

describe('scopeTags', () => {
  it('matches the tags the ingester syncs under', () => {
    expect(scopeTags({ type: 'confluence', spaces: ['eng', '~jdoe'] })).toEqual(['confluence:ENG', 'confluence:~jdoe'])
    expect(scopeTags({ type: 'jira', projects: [' ops '] })).toEqual(['jira:OPS'])
    expect(scopeTags({ type: 'web', urls: ['https://Docs.Example.com:443/a/', 'https://docs.example.com/b/', 'http://build:8080/'] }))
      .toEqual(['web:docs.example.com', 'web:build:8080'])
  })

  it('skips a URL that does not parse yet', () => {
    expect(scopeTags({ type: 'web', urls: ['docs.example'] })).toEqual([])
  })
})

describe('git sources', () => {
  it('round-trips a stored git source and omits defaults', () => {
    const stored: ConnectorSource = {
      type: 'git', name: 'app', url: 'https://gitlab.example.com/g/app.git', token_env: 'GITLAB_TOKEN',
      branch: 'develop', paths: ['docs/', 'src'], exclude: ['*.lock'], max_file_kb: 64, collection: 'mnemos_knowledge',
    }
    expect(toPayload(toForm(stored))).toEqual(stored)
    // A public repository needs no token; default size and collection are not written.
    expect(toPayload({ ...newSource('git'), url: 'https://github.com/acme/app' }))
      .toEqual({ type: 'git', url: 'https://github.com/acme/app' })
  })

  it('syncs under the tag repo_scope computes', () => {
    expect(scopeTags({ type: 'git', url: 'https://GitHub.com/acme/app.git' })).toEqual(['git:github.com/acme/app'])
    expect(scopeTags({ type: 'git', url: 'https://git.local:8443/g/sub/r/' })).toEqual(['git:git.local:8443/g/sub/r'])
    expect(scopeTags({ type: 'git', url: 'git@github.com:acme/app.git' })).toEqual([])
  })
})

describe('sync status', () => {
  const state = {
    acked_id: 2,
    requests: [
      { id: 2, sources: null, requested_at: '2026-11-23T10:00:00+00:00' },
      { id: 3, sources: ['wiki'], requested_at: '2026-11-23T10:00:10+00:00' },
    ],
  }

  it('is pending only for sources an unacknowledged request names', () => {
    expect(syncPending(state, 'wiki')).toBe(true)
    // Request 2, for every source, was already picked up.
    expect(syncPending(state, 'tracker')).toBe(false)
    expect(syncPending({ ...state, acked_id: 1 }, 'tracker')).toBe(true)
  })

  it('calls a request stalled once it has waited past the threshold', () => {
    const sent = Date.parse('2026-11-23T10:00:10+00:00')
    expect(syncStalled(state, sent + 29_000)).toBe(false)
    expect(syncStalled(state, sent + 31_000)).toBe(true)
    expect(syncStalled({ ...state, acked_id: 3 }, sent + 60_000)).toBe(false)
  })

  it('names an unnamed source after its type, as the sync service does', () => {
    expect(sourceName({ type: 'web', name: '  ' })).toBe('web')
    expect(sourceName({ type: 'git', name: 'Lpel - Lib - Auth' })).toBe('Lpel - Lib - Auth')
  })
})
