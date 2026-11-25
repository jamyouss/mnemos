import { describe, expect, it } from 'vitest'
import {
  ago, newSource, oldestPending, pendingSummary, runSummary, scopeTags, serviceHealth, serviceSummary, sourceMatches,
  sourceName, syncStalled, toForm, toPayload, waitingLabel,
  type ConnectorSource,
} from './connectors'

describe('sourceMatches', () => {
  const auth = toForm({
    type: 'git', name: 'LPEL - Lib - auth', url: 'https://gitlab.example.com/lpel/front/libraries/auth', tags: ['lpel-front-libs'],
  })

  it('needs every word, in any field, whatever the case', () => {
    expect(sourceMatches(auth, 'lib AUTH')).toBe(true)
    expect(sourceMatches(auth, 'libraries lpel-front-libs')).toBe(true)
    expect(sourceMatches(auth, 'lib checkout')).toBe(false)
  })

  it('lets everything through on a blank query, and finds an unnamed source by its type', () => {
    expect(sourceMatches(auth, '  ')).toBe(true)
    expect(sourceMatches(newSource('jira'), 'jira')).toBe(true)
  })
})

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

  it('finds the oldest request a source or "Sync all" waits on, and words it', () => {
    const waiting = {
      acked_id: 1,
      requests: [
        { id: 2, sources: ['wiki'], requested_at: '2026-11-23T10:00:00+00:00', requested_by: 'admin' },
        { id: 3, sources: null, requested_at: '2026-11-23T10:05:00+00:00', requested_by: 'ops' },
      ],
    }
    expect(oldestPending(waiting, 'wiki')?.id).toBe(2)
    expect(oldestPending(waiting, 'tracker')?.id).toBe(3)
    expect(oldestPending(waiting)?.id).toBe(3)
    expect(oldestPending({ ...waiting, acked_id: 3 }, 'wiki')).toBeUndefined()
    const now = Date.parse('2026-11-23T10:13:00+00:00')
    expect(waitingLabel(waiting.requests[0]!, now)).toBe('Waiting — requested 13 min ago by admin')
    expect(pendingSummary(waiting, now)).toBe('2 requests waiting, oldest sent 13 min ago')
    expect(pendingSummary({ ...waiting, acked_id: 3 }, now)).toBe('')
  })
})

describe('sync service and runs', () => {
  const now = Date.parse('2026-11-24T10:00:00Z')

  it('tells a service that never reported from one that went silent', () => {
    expect(serviceHealth(null, now)).toBe('never')
    expect(serviceHealth('2026-11-24T09:58:30Z', now)).toBe('alive')
    expect(serviceHealth('2026-11-24T09:56:00Z', now)).toBe('silent')
  })

  it('words the sync service health in one line', () => {
    expect(serviceSummary(null, now)).toEqual({ health: 'never', text: 'No sync service has reported to this server' })
    expect(serviceSummary('2026-11-24T09:47:00Z', now)).toEqual({ health: 'silent', text: 'Sync service silent, last heard from 13 min ago' })
  })

  it('says what the last sync of a source did and when', () => {
    expect(runSummary({ state: 'running', started_at: '2026-11-24T09:56:00Z', finished_at: null }, now))
      .toBe('syncing, started 4 min ago')
    expect(runSummary({ state: 'ok', started_at: null, finished_at: '2026-11-24T07:30:00Z' }, now)).toBe('synced 2 h ago')
    expect(runSummary({ state: 'failed', started_at: null, finished_at: '2026-11-24T09:59:40Z' }, now)).toBe('sync failed just now')
    expect(runSummary(undefined, now)).toBe('')
    expect(ago('2026-11-24T10:00:30Z', now)).toBe('just now') // a server clock slightly ahead
  })
})
