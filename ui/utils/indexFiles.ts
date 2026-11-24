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

export const shortPath = (p: string) => p.replace('/data/codebase/', '')

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
