import { describe, expect, it } from 'vitest'
import { canReadSetup, describeScope, isAdmin, sameScope, tenantNameProblem, toScope, toScopeForm, type Whoami } from './tenants'

describe('scope encoding', () => {
  it('sends "*" for all projects, whatever tags were picked before', () => {
    expect(toScope({ all: true, tags: 'acme, go' })).toBe('*')
  })

  it('sends the picked tags as a list, without repeats', () => {
    expect(toScope({ all: false, tags: 'acme, go, acme' })).toEqual(['acme', 'go'])
  })

  it('sends an empty list, not "*", when nothing is picked', () => {
    // An empty write scope is read-only; turning it into "*" would grant everything.
    expect(toScope({ all: false, tags: ' , ' })).toEqual([])
  })

  it('round-trips both forms', () => {
    expect(toScope(toScopeForm('*'))).toBe('*')
    expect(toScope(toScopeForm(['acme', 'team one']))).toEqual(['acme', 'team one'])
  })
})

describe('sameScope', () => {
  it('ignores tag order but not "*" versus a list', () => {
    expect(sameScope(['a', 'b'], ['b', 'a'])).toBe(true)
    expect(sameScope('*', '*')).toBe(true)
    expect(sameScope('*', ['a'])).toBe(false)
    expect(sameScope([], '*')).toBe(false)
    expect(sameScope(['a'], ['a', 'b'])).toBe(false)
  })
})

describe('describeScope', () => {
  it('says what the scope grants', () => {
    expect(describeScope('*')).toBe('All projects')
    expect(describeScope(['acme', 'go'])).toBe('acme, go')
    expect(describeScope([])).toBe('None')
  })
})

describe('tenantNameProblem', () => {
  it('accepts what the server accepts', () => {
    expect(tenantNameProblem('ci-acme_1.0', ['admin'])).toBeNull()
    expect(tenantNameProblem('x'.repeat(64), [])).toBeNull()
  })

  it('refuses empty, invalid, too long and taken names', () => {
    expect(tenantNameProblem('', [])).not.toBeNull()
    expect(tenantNameProblem('has space', [])).not.toBeNull()
    expect(tenantNameProblem('x'.repeat(65), [])).not.toBeNull()
    expect(tenantNameProblem('admin', ['admin'])).toMatch(/already exists/)
  })
})

describe('access', () => {
  const me = (admin: boolean, write: Whoami['write']): Whoami => ({ name: 'n', admin, read: '*', write, auth_enabled: true })

  it('hides nothing while the caller is unknown', () => {
    expect(isAdmin(null)).toBe(true)
    expect(canReadSetup(undefined)).toBe(true)
  })

  it('lets a key that writes every tag read the setup, but only an admin manage it', () => {
    expect(isAdmin(me(false, '*'))).toBe(false)
    expect(canReadSetup(me(false, '*'))).toBe(true)
    expect(canReadSetup(me(false, ['acme']))).toBe(false)
    expect(isAdmin(me(true, []))).toBe(true)
  })
})
