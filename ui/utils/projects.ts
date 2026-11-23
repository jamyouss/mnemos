/**
 * The path prefix → tags mapping as the Projects page edits it: one row per
 * prefix, with the ignore rules that hold for that prefix only.
 */
import { splitList } from './connectors'
import { splitFilterList } from './playground'

/** One prefix as the server stores and returns it. */
export interface ProjectPath {
  prefix: string
  tags: string[]
  exclude_dirs: string[]
  exclude_exts: string[]
}

/** Tags stay the comma-separated string FacetCombobox edits; the ignore lists are free text. */
export interface ProjectRowForm {
  id: number
  prefix: string
  tags: string
  excludeDirs: string
  excludeExts: string
}

let nextId = 0

export function newRow(): ProjectRowForm {
  return { id: nextId++, prefix: '', tags: '', excludeDirs: '', excludeExts: '' }
}

export function toRowForm(path: ProjectPath): ProjectRowForm {
  return {
    id: nextId++,
    prefix: path.prefix,
    tags: path.tags.join(', '),
    excludeDirs: path.exclude_dirs.join(', '),
    excludeExts: path.exclude_exts.join(', '),
  }
}

/** Order is kept: the first tag is the project's primary label. */
export function toPathPayload(row: ProjectRowForm): ProjectPath {
  return {
    prefix: row.prefix.trim(),
    tags: [...new Set(splitFilterList(row.tags))],
    exclude_dirs: splitList(row.excludeDirs),
    exclude_exts: splitList(row.excludeExts),
  }
}

/** The first two path segments, where one organisation's projects usually sit. */
export function prefixGroup(prefix: string): string {
  const parts = prefix.split('/').filter(Boolean)
  return parts.length > 2 ? `${parts.slice(0, 2).join('/')}/` : ''
}

export interface RowGroup<T> {
  /** '' gathers the prefixes too short to share a group. */
  key: string
  rows: T[]
}

/** Rows matching `query` on prefix or tag, grouped by `prefixGroup` and sorted by prefix. */
export function groupRows<T extends Pick<ProjectRowForm, 'prefix' | 'tags'>>(rows: T[], query: string): RowGroup<T>[] {
  const q = query.trim().toLowerCase()
  const groups = new Map<string, T[]>()
  for (const row of rows) {
    if (q && !row.prefix.toLowerCase().includes(q) && !row.tags.toLowerCase().includes(q)) continue
    const key = prefixGroup(row.prefix.trim())
    groups.set(key, [...(groups.get(key) ?? []), row])
  }
  return [...groups]
    .sort(([a], [b]) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)))
    .map(([key, list]) => ({ key, rows: list.sort((x, y) => x.prefix.localeCompare(y.prefix)) }))
}

export interface RowProblems {
  /** Keyed by the row's position in the list that was validated. */
  byRow: Map<number, string[]>
  general: string[]
}

const ROW_PROBLEM = /^paths\[(\d+)\]:\s*(.*)$/s

/** Splits the server's `paths[<i>]: <message>` problems onto their rows; anything else is general. */
export function problemsByRow(problems: string[]): RowProblems {
  const byRow = new Map<number, string[]>()
  const general: string[] = []
  for (const problem of problems) {
    const match = ROW_PROBLEM.exec(problem)
    if (!match) {
      general.push(problem)
      continue
    }
    const index = Number(match[1])
    byRow.set(index, [...(byRow.get(index) ?? []), match[2]!])
  }
  return { byRow, general }
}
