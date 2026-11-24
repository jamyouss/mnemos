/** The Memories page's create and edit form. */
import type { MemoryChange, MemoryEntry, MemoryType } from '~/composables/useApi'

export const MEMORY_TYPES: MemoryType[] = ['decision', 'pattern', 'lesson', 'convention', 'note']

/** The types offered, plus `current` when an extractor wrote another one (e.g. `fix`). */
export function typeOptions(current: string): string[] {
  return MEMORY_TYPES.includes(current as MemoryType) ? MEMORY_TYPES : [...MEMORY_TYPES, current]
}

export interface MemoryForm {
  content: string
  memory_type: string
  project: string
  /** Comma- or space-separated, as typed. */
  tags: string
}

/** Tags typed with commas, spaces or both; repeats dropped, order kept. */
export function splitTags(text: string): string[] {
  return [...new Set(text.split(/[\s,]+/).filter(Boolean))]
}

export function toMemoryForm(m: MemoryEntry): MemoryForm {
  return { content: m.content, memory_type: m.memory_type, project: m.project ?? '', tags: m.tags.join(', ') }
}

/** Only what the form changed, so an edit never re-sends a field someone else just set. */
export function memoryChange(original: MemoryEntry, form: MemoryForm): MemoryChange {
  const out: MemoryChange = {}
  const content = form.content.trim()
  if (content !== original.content) out.content = content
  if (form.memory_type !== original.memory_type) out.memory_type = form.memory_type
  const project = form.project.trim()
  if (project !== (original.project ?? '')) out.project = project
  const tags = splitTags(form.tags)
  if (tags.join('\n') !== original.tags.join('\n')) out.tags = tags
  return out
}
