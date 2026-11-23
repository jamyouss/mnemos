import { describe, expect, it } from 'vitest'
import { buildBody, type PlaygroundForm, toCurl, toMcpCall, validationMessage } from './playground'

const form = (over: Partial<PlaygroundForm> = {}): PlaygroundForm => ({
  target: 'search-code',
  query: '  cache invalidation ',
  limit: 10,
  mode: 'preview',
  trace: true,
  bypassCache: true,
  collections: [],
  tagsAny: '',
  tagsAll: '',
  fileTypes: '',
  pathFilter: '',
  language: '',
  symbolType: '',
  memoryType: '',
  ...over,
})

describe('buildBody', () => {
  it('sends only the filters the endpoint accepts, and none left empty', () => {
    const body = buildBody(form({ language: 'python', memoryType: 'lesson', fileTypes: 'go', tagsAny: 'a, ,b' }))
    expect(body).toEqual({
      query: 'cache invalidation',
      limit: 10,
      tags_any: ['a', 'b'],
      language: 'python',
      mode: 'preview',
      trace: true,
      source: 'playground',
    })
  })

  it('asks /api/search to skip its cache, and only that endpoint', () => {
    expect(buildBody(form({ target: 'search' })).use_cache).toBe(false)
    expect(buildBody(form({ target: 'search', bypassCache: false })).use_cache).toBe(true)
    expect(buildBody(form({ target: 'search-memory' }))).not.toHaveProperty('use_cache')
  })
})

describe('replayed calls', () => {
  const body = buildBody(form({ target: 'search', query: "what's cached" }))

  it('drop the Playground-only fields, so a replay is ordinary traffic', () => {
    const mcp = toMcpCall('search', body)
    expect(mcp.startsWith('mnemos_search(')).toBe(true)
    for (const key of ['trace', 'source', 'use_cache']) expect(mcp).not.toContain(key)
  })

  it('quote JSON safely for the shell and never inline the API key', () => {
    const curl = toCurl('http://localhost:8100', 'search', body, true)
    expect(curl).toContain(`what'\\''s cached`)
    expect(curl).toContain('$MNEMOS_API_KEY')
    expect(toCurl('http://localhost:8100', 'search', body, false)).not.toContain('authorization')
  })
})

describe('validationMessage', () => {
  it('names the refused field from a FastAPI 422 list', () => {
    const detail = [{ loc: ['body', 'limit'], msg: 'Input should be less than or equal to 50' }]
    expect(validationMessage(detail)).toBe('limit: Input should be less than or equal to 50')
  })

  it('keeps a plain sentence and gives up on anything else', () => {
    expect(validationMessage('Unknown collection')).toBe('Unknown collection')
    expect(validationMessage(null)).toBeNull()
    expect(validationMessage([{ nope: 1 }])).toBeNull()
  })
})
