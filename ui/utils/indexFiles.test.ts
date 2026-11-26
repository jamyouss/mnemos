import { describe, expect, it } from 'vitest'
import { displayTags, fileLabel, indexFileUrl, skipExplanation } from './indexFiles'

describe('indexFileUrl', () => {
  it('keeps the leading slash of a server path, so the route sees it whole', () => {
    expect(indexFileUrl('mnemos_code', '/data/codebase/acme/main.go')).toBe('/api/index/mnemos_code//data/codebase/acme/main.go')
  })

  it('encodes each segment but keeps the slashes', () => {
    expect(indexFileUrl('mnemos_knowledge', 'confluence/ENG/a page?#1')).toBe('/api/index/mnemos_knowledge/confluence/ENG/a%20page%3F%231')
  })
})

describe('skipExplanation', () => {
  it('names the rule, then the reason', () => {
    expect(skipExplanation({ rule: 'built-in', project_prefix: null, reason: 'node_modules' })).toBe('A built-in rule skips it: node_modules.')
    expect(skipExplanation({ rule: 'project', project_prefix: 'acme/web', reason: null })).toBe('The ignore rules of project acme/web skip it.')
  })

  it('explains a path outside every declared project', () => {
    expect(skipExplanation({ rule: 'declared-only', project_prefix: null, reason: null })).toMatch(/not under any declared project prefix/)
  })
})

describe('fileLabel', () => {
  it('reads a git file as its name in its repository and folder', () => {
    expect(fileLabel('git/gitlab.example.com/lpel~pnx-phoenix~front~libraries~utils/src/lib/date.ts'))
      .toEqual({ name: 'date.ts', place: 'libraries/utils › src/lib' })
    expect(fileLabel('git/github.com/acme~app/README.md')).toEqual({ name: 'README.md', place: 'acme/app' })
  })

  it('names a web page by its address, a mounted file by its folder', () => {
    expect(fileLabel('web/docs.example.com/3f2a.md', 'https://docs.example.com/guide/install'))
      .toEqual({ name: 'install', place: 'docs.example.com' })
    expect(fileLabel('/data/codebase/acme/web/app.ts')).toEqual({ name: 'app.ts', place: 'acme/web' })
  })
})

describe('displayTags', () => {
  it('drops connector markers and counts what does not fit', () => {
    const tags = ['lpel-front-lib-utils', 'lpel', 'lpel-front', 'laposte', 'git', 'git:gitlab.example.com/lpel/utils']
    expect(displayTags(tags)).toEqual({ shown: ['lpel-front-lib-utils', 'lpel', 'lpel-front'], more: ['laposte'] })
  })
})
