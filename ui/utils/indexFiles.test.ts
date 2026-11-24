import { describe, expect, it } from 'vitest'
import { indexFileUrl, skipExplanation } from './indexFiles'

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
