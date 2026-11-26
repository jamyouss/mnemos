/** How the Index page names files, collections and path-check verdicts. */

export const INDEX_COLLECTIONS = ['mnemos_code', 'mnemos_knowledge', 'mnemos_docs', 'mnemos_skills', 'mnemos_memory'] as const

/** Collections whose chunks carry project tags; skills and docs have none. */
export const TAGGED_COLLECTIONS: readonly string[] = ['mnemos_code', 'mnemos_knowledge', 'mnemos_memory']

/** Collections a server path can be reindexed into, with where their files live in the container. */
export const REINDEX_ROOTS: Record<string, string> = {
  mnemos_code: '/data/codebase/',
  mnemos_docs: '/data/claude-config/docs',
  mnemos_skills: '/data/claude-config/skills',
}

/** A server path without its mount: `/data/codebase/a.ts` reads `a.ts`, a skill reads `skills/…`. */
export const shortPath = (p: string) => p.replace(/^\/data\/(codebase|claude-config)\//, '')

/** Tags every connector adds (`git`, `git:<scope>`…): the file's path already says it. */
const SOURCE_TAGS: Record<string, true> = { git: true, web: true, jira: true, confluence: true }

export interface FileLabel {
  /** The file's own name. */
  name: string
  /** Where it lives: repository, space or host, then the folder inside it. */
  place: string
}

/**
 * A stored path split for reading: `git/<host>/<owner~repo>/src/a.ts` reads
 * `a.ts` in `owner/repo › src`; a mounted file reads its name in its folder.
 */
export function fileLabel(filePath: string, sourceUrl?: string | null): FileLabel {
  const parts = shortPath(filePath).split('/').filter(Boolean)
  const name = parts.pop() ?? filePath
  const [kind, ...rest] = parts
  if (kind === 'git' && rest.length >= 2) {
    // `<owner~sub~repo>` keeps a repository in one segment; its last two levels name it.
    const repo = rest[1]!.split('~').slice(-2).join('/')
    return { name, place: [repo, rest.slice(2).join('/')].filter(Boolean).join(' › ') }
  }
  if (kind === 'web' && sourceUrl) {
    // Web pages are stored under a hash: the page's own address names them.
    const url = new URL(sourceUrl)
    return { name: url.pathname.split('/').filter(Boolean).pop() ?? url.host, place: url.host }
  }
  if ((kind === 'confluence' || kind === 'jira') && rest.length) return { name, place: `${kind} › ${rest.join('/')}` }
  return { name, place: parts.join('/') }
}

/** The tags worth reading in a list: connector markers dropped, the first `max` shown, the rest counted. */
export function displayTags(tags: string[], max = 3): { shown: string[]; more: string[] } {
  const useful = tags.filter((t) => !SOURCE_TAGS[t] && !t.includes(':'))
  return { shown: useful.slice(0, max), more: useful.slice(max) }
}

/** DELETE URL of a file: slashes kept, each segment encoded, so `/data/x` stays a path. */
export function indexFileUrl(collection: string, filePath: string): string {
  return `/api/index/${encodeURIComponent(collection)}/${filePath.split('/').map(encodeURIComponent).join('/')}`
}

const CHUNKERS: Record<string, string> = {
  go: 'Go (syntax tree)',
  vue: 'Vue single-file component',
  markdown: 'Markdown headings',
  tabular: 'Table rows',
  fallback: 'Plain text windows',
}

export const chunkerLabel = (chunker: string) => CHUNKERS[chunker] ?? chunker

/** Why a path is not indexed, in words: the rule that skips it, then the server's reason. */
export function skipExplanation(check: { rule: string | null; project_prefix: string | null; reason: string | null }): string {
  const rule = check.rule === 'built-in'
    ? 'A built-in rule skips it'
    : check.rule === 'project'
      ? `The ignore rules of project ${check.project_prefix ?? '(unknown)'} skip it`
      : check.rule === 'declared-only'
        ? 'It is not under any declared project prefix, and only declared projects are indexed'
        : 'It is skipped'
  return check.reason ? `${rule}: ${check.reason}.` : `${rule}.`
}
