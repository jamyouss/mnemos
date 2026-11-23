/**
 * Tenants and their scopes as the Tenants page edits them. A scope is `"*"`
 * (every tag, present and future) or an explicit tag list; an empty list
 * grants nothing.
 */
import { splitFilterList } from './playground'

export type Scope = '*' | string[]

/** A tenant as the server lists it: never its key, not even hashed. */
export interface Tenant {
  name: string
  admin: boolean
  /** Set from MNEMOS_BOOTSTRAP_ADMIN_KEY; the server refuses to edit, rotate or delete it. */
  root: boolean
  read: Scope
  write: Scope
  created_at: string
}

/** The caller as `GET /api/whoami` describes it; with auth off, an admin. */
export interface Whoami {
  name: string
  admin: boolean
  read: Scope
  write: Scope
  auth_enabled: boolean
}

// Unknown caller (whoami still loading or failed): nothing is hidden, the
// server's own 403 decides. The UI only ever narrows what it shows.
export const isAdmin = (me: Whoami | null | undefined) => !me || me.admin

/** Connectors and projects can also be read by a key that writes every tag (the ingester, scripts). */
export const canReadSetup = (me: Whoami | null | undefined) => !me || me.admin || me.write === '*'

/** `tags` stays the comma-separated string FacetCombobox edits. */
export interface ScopeForm {
  all: boolean
  tags: string
}

export const TENANT_NAME = /^[A-Za-z0-9_.-]{1,64}$/

export function toScopeForm(scope: Scope): ScopeForm {
  return scope === '*' ? { all: true, tags: '' } : { all: false, tags: scope.join(', ') }
}

export function toScope(form: ScopeForm): Scope {
  return form.all ? '*' : [...new Set(splitFilterList(form.tags))]
}

export function sameScope(a: Scope, b: Scope): boolean {
  if (a === '*' || b === '*') return a === b
  return a.length === b.length && [...a].sort().join('\n') === [...b].sort().join('\n')
}

export function describeScope(scope: Scope): string {
  if (scope === '*') return 'All projects'
  return scope.length ? scope.join(', ') : 'None'
}

/** Why a new tenant name would be refused, before the server says so. */
export function tenantNameProblem(name: string, existing: string[]): string | null {
  if (!name) return 'A name is required.'
  if (!TENANT_NAME.test(name)) return 'Letters, digits, “_”, “.” and “-” only, at most 64 characters.'
  if (existing.includes(name)) return `A tenant named “${name}” already exists.`
  return null
}
