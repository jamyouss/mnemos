import { describe, expect, it } from 'vitest'
import { newSource, scopeTags, toForm, toPayload, type ConnectorSource } from './connectors'

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
