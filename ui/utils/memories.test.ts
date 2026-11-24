import { describe, expect, it } from 'vitest'
import type { MemoryEntry } from '~/composables/useApi'
import { memoryChange, splitTags, toMemoryForm, typeOptions } from './memories'

describe('splitTags', () => {
  it('accepts commas, spaces or both, without repeats', () => {
    expect(splitTags('acme, go  vue3,acme')).toEqual(['acme', 'go', 'vue3'])
    expect(splitTags(' , ')).toEqual([])
  })
})

describe('memoryChange', () => {
  const entry: MemoryEntry = {
    id: 'x', content: 'Use RRF', memory_type: 'decision', project: 'acme', tags: ['acme', 'search'], status: 'approved',
  }

  it('sends nothing when the form is unchanged', () => {
    expect(memoryChange(entry, toMemoryForm(entry))).toEqual({})
  })

  it('sends only the fields that changed', () => {
    const form = { ...toMemoryForm(entry), content: ' Use RRF k=60 ', tags: 'acme search go' }
    expect(memoryChange(entry, form)).toEqual({ content: 'Use RRF k=60', tags: ['acme', 'search', 'go'] })
  })

  it('clears the project with an empty string', () => {
    expect(memoryChange(entry, { ...toMemoryForm(entry), project: '  ' })).toEqual({ project: '' })
  })

  it('keeps a type the form does not offer, unless changed', () => {
    const fix = { ...entry, memory_type: 'fix' }
    expect(memoryChange(fix, { ...toMemoryForm(fix), content: 'Use RRF k=60' })).toEqual({ content: 'Use RRF k=60' })
    expect(typeOptions('fix')).toContain('fix')
  })
})
